"""Conservative, dependency-free article extraction. Never treats a whole page as a body."""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit, urldefrag

from .network import SafeHTTPClient, SourceNetworkError

MAX_BODY = 48000
READER_VERSION = 2


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.blocks, self.jsonld = [], [], []
        self.active, self.skip, self.json_depth = 0, 0, False
        self.buffer, self.json_buffer = [], []
        self.regions, self.captures = [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = values.get('class', '').split()
        ignored = (tag in ('script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'button')
                   or bool(re.search(r'(^|[-_\s])(comments?|related-posts|social-share)([-_\s]|$)', values.get('class', ''), re.I)))
        priority = (3 if values.get('itemprop') == 'articleBody' or any(c in classes for c in
                    ('blog-content', 'article-content', 'post-content', 'entry-content', 'ltx_document')) else
                    2 if tag == 'article' else 1 if tag == 'main' or values.get('role') == 'main' else 0)
        root = priority > 0
        void = tag in ('br', 'img', 'meta', 'link', 'input', 'hr', 'source', 'wbr', 'embed', 'area', 'base', 'col', 'param', 'track')
        if tag == 'script' and values.get('type', '').lower() == 'application/ld+json':
            self.json_depth, self.json_buffer = True, []
        if not void:
            capture = [] if root else None
            if root:
                self.regions.append((priority, capture))
                self.captures.append(capture)
            self.stack.append((tag, root, ignored, capture))
            self.active += int(root)
            self.skip += int(ignored)
        if tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'li', 'br', 'tr'):
            self.buffer.append('\n')
            for capture in self.captures:
                capture.append('\n')

    def handle_endtag(self, tag):
        if tag == 'script' and self.json_depth:
            self.jsonld.append(''.join(self.json_buffer))
            self.json_depth = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, root, ignored, capture in self.stack[i:]:
                    self.active -= int(root)
                    self.skip -= int(ignored)
                    if root:
                        self.captures = [c for c in self.captures if c is not capture]
                del self.stack[i:]
                break
        if tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'li', 'tr'):
            self.buffer.append('\n')
            for capture in self.captures:
                capture.append('\n')

    def handle_data(self, data):
        if self.json_depth:
            self.json_buffer.append(data)
        if self.active and not self.skip:
            self.buffer.append(data)
            for capture in self.captures:
                capture.append(data)


def extract_body(html):
    parser = ArticleHTML()
    parser.feed(html)
    candidates = []
    for encoded in parser.jsonld:
        try:
            values = json.loads(encoded)
        except (ValueError, RecursionError):
            continue
        pending = [values]
        for _ in range(100):
            if not pending:
                break
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node[:100])
            elif isinstance(node, dict):
                if isinstance(node.get('articleBody'), str):
                    candidates.append(node['articleBody'])
                graph = node.get('@graph')
                if isinstance(graph, list):
                    pending.extend(graph[:100])
    if not any(len(c) >= 200 for c in candidates):
        regions = [(p, ''.join(parts)) for p, parts in parser.regions if len(''.join(parts).strip()) >= 200]
        priority = max((p for p, _ in regions), default=0)
        candidates = [text for p, text in regions if p == priority]
    candidates = ['\n'.join(re.sub(r'[\t \r\f]+', ' ', p).strip() for p in text.splitlines()
                            if p.strip()) for text in candidates]
    return max(candidates, key=len, default='')


def article_url(record):
    url = urldefrag(record.url)[0]
    parts = urlsplit(url)
    if parts.hostname in ('arxiv.org', 'export.arxiv.org') and parts.path.startswith('/abs/'):
        return 'https://arxiv.org/html/' + parts.path[5:]
    return url


class ArticleReader:
    def __init__(self, http=None):
        self.http = http or SafeHTTPClient(timeout=12, max_bytes=2_000_000)

    def read(self, record):
        url = article_url(record)
        host = urlsplit(url).hostname or ''
        if record.is_fixture or host in ('example.org', 'example.com', 'example.net') or host.endswith('.example.org'):
            return {'status': 'unavailable', 'reason': 'test_or_placeholder', 'text': '', 'url': url}
        try:
            response = self.http.get_page(url)
            if 'html' not in response.content_type.lower():
                return {'status': 'unavailable', 'reason': 'unsupported_content_type', 'text': '', 'url': url}
            charset = re.search(r'charset=["\x27]?([\w-]+)', response.content_type, re.I)
            try:
                html = response.body.decode(charset.group(1) if charset else 'utf-8')
            except (UnicodeError, LookupError):
                return {'status': 'unavailable', 'reason': 'decode_failed', 'text': '', 'url': url}
            text = extract_body(html)
            if len(text) < 200:
                return {'status': 'unavailable', 'reason': 'body_unconfirmed', 'text': '', 'url': url}
            if len(text) > MAX_BODY:
                return {'status': 'oversized', 'reason': 'body_limit', 'text': '', 'url': url,
                        'characters': len(text)}
            return {'status': 'extracted', 'reason': '', 'text': text,
                    'url': response.final_url or url, 'characters': len(text)}
        except SourceNetworkError as exc:
            return {'status': 'unavailable', 'reason': exc.code, 'text': '', 'url': url}
