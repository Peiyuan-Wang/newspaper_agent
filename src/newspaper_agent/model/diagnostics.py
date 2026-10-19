"""Sanitized transport and protocol failures; never echo upstream text."""
import time
import httpx


def transport_failure(exc):
    for cls, code, text in (
        (httpx.ConnectTimeout, 'connect_timeout', '连接模型服务超时；尚未建立连接，请检查服务地址或代理。'),
        (httpx.ReadTimeout, 'read_timeout', '等待模型响应超时；服务未在读取等待上限内返回数据。可提高等待时间，或降低输出额度。'),
        (httpx.WriteTimeout, 'write_timeout', '发送模型请求超时；请检查上传链路或代理。'),
        (httpx.PoolTimeout, 'pool_timeout', '等待本地连接资源超时；不是模型回复错误。'),
        (httpx.ProxyError, 'proxy_error', '代理连接失败；请检查代理是否可达及其配置。'),
        (httpx.ConnectError, 'connect_error', '无法连接模型服务；可能为 DNS、TLS 或连接拒绝，请检查服务地址与代理。'),
        (httpx.RemoteProtocolError, 'remote_protocol', '服务或代理中断了响应，或返回无效 HTTP 协议；请稍后重试并检查中转站。'),
        (httpx.ReadError, 'read_error', '接收模型数据时连接中断；可能为代理或上游线路重置。'),
        (httpx.WriteError, 'write_error', '发送模型数据时连接中断；请检查上传链路或代理。'),
    ):
        if isinstance(exc, cls):
            return code, text
    return 'transport_error', '模型传输失败；未取得完整响应，请检查服务与代理。'


def read_bounded(client, endpoint, *, headers, payload, timeout, control=None):
    """Buffer bounded JSON; timeout=None disables the optional total deadline."""
    started = time.monotonic()
    data = bytearray()
    with client.stream('POST', endpoint, headers=headers, json=payload) as incoming:
        incoming.raise_for_status()
        if 'text/event-stream' in incoming.headers.get('content-type', ''):
            raise ValueError('unexpected_event_stream')
        for chunk in incoming.iter_bytes():
            if control:
                control.check()
                if not data:
                    control.emit('model_receiving')
            if timeout is not None and time.monotonic() - started > timeout:
                raise ValueError('response_deadline')
            data.extend(chunk)
            if len(data) > 4_000_000:
                raise ValueError('response_too_large')
        # Construct a buffered response for the existing pure parsers.
        headers = {k: v for k, v in incoming.headers.items() if k.lower() not in ('content-encoding', 'content-length')}
        return httpx.Response(incoming.status_code, headers=headers,
                              content=bytes(data), request=incoming.request)
