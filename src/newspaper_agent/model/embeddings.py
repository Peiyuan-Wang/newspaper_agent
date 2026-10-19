"""Pinned local multilingual encoder. Download is explicit; inference is offline."""
from functools import lru_cache
from pathlib import Path
from threading import RLock
import math

MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
REPO = 'Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q'
REVISION = 'faf4aa4225822f3bc6376869cb1164e8e3feedd0'
FILES = ('model_optimized.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
         'special_tokens_map.json', 'unigram.json', 'ort_config.json')
VERSION = 'minilm384-mean-unit-v1:' + REVISION


def model_directory(data_directory):
    return Path(data_directory) / 'embedding_models' / REVISION


def model_ready(data_directory):
    folder = model_directory(data_directory)
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in FILES)


def download_model(data_directory):
    from huggingface_hub import snapshot_download
    folder = model_directory(data_directory)
    snapshot_download(REPO, revision=REVISION, local_dir=str(folder), allow_patterns=list(FILES),
                      token=False, max_workers=2)
    if not model_ready(data_directory):
        raise ValueError('语义模型文件未下载完整，请重试准备模型')
    return str(folder)


class LocalTextEncoder:
    version, dimension = VERSION, 384

    def __init__(self, data_directory):
        if not model_ready(data_directory):
            raise ValueError('请先在兴趣与领域中准备本地语义模型')
        from fastembed import TextEmbedding
        self.model = TextEmbedding(MODEL, specific_model_path=str(model_directory(data_directory)),
                                   local_files_only=True, threads=2, providers=['CPUExecutionProvider'])
        self.lock = RLock()

    def encode(self, texts):
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() or len(text) > 2000 for text in texts):
            raise ValueError('语义编码文字无效')
        with self.lock:
            arrays = list(self.model.embed(list(texts), batch_size=32, parallel=None))
        result = []
        for array in arrays:
            row = [float(v) for v in array]
            length = math.sqrt(sum(v*v for v in row))
            if len(row) != self.dimension or not math.isfinite(length) or length < 1e-8:
                raise ValueError('语义编码结果无效')
            result.append([v/length for v in row])
        if len(result) != len(texts):
            raise ValueError('语义编码结果不完整')
        return result


@lru_cache(maxsize=2)
def local_encoder(data_directory):
    return LocalTextEncoder(data_directory)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='准备本地多语言语义模型，不调用聊天API')
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    data = Path(__file__).resolve().parents[3] / 'data'
    if args.download:
        download_model(data)
    encoder = local_encoder(str(data))
    vectors = encoder.encode(['群论与投影表示', 'group theory and projective representations'])
    print({'model': MODEL, 'dimension': len(vectors[0]), 'cross_language_similarity':
           round(sum(a*b for a,b in zip(*vectors)), 4)})
