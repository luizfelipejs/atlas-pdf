"""Local Sentence Transformer inference, with the model's ONNX CPU export.

No network access at runtime. Mean pooling and normalization reproduce the
SentenceTransformer pipeline; quantized weights keep the desktop package small.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
MODEL_VERSION = f"{MODEL_NAME}@{MODEL_REVISION}:quint8-avx2:80w:128t:96chunks:v1"
WORDS_PER_CHUNK = 80
MAX_CHUNKS = 96
DIMENSIONS = 384


class SemanticUnavailable(RuntimeError):
    pass


def clean_text(text: str) -> str:
    for match in re.finditer(r"(?im)^\s*(?:references|bibliography|referencias|bibliografia)\s*$", text):
        if match.start() > len(text) * .65:
            text = text[:match.start()]
            break
    return re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)


def select_chunks(chunks: list, maximum: int = MAX_CHUNKS) -> list:
    if len(chunks) <= maximum:
        return chunks
    # Include both ends and distribute the budget over the whole extracted text.
    return [chunks[round(i * (len(chunks) - 1) / (maximum - 1))] for i in range(maximum)]


def text_chunks(text: str) -> list[str]:
    words = clean_text(text).split()
    if not words:
        raise ValueError("Livro sem texto")
    return select_chunks([" ".join(words[i:i + WORDS_PER_CHUNK])
                          for i in range(0, len(words), WORDS_PER_CHUNK)])


def vector_key(text: str) -> str:
    return hashlib.sha256((MODEL_VERSION + "\0" + clean_text(text)).encode("utf-8")).hexdigest()


def numpy_module():
    # Development dependencies are isolated from the user's Python installation.
    local = ROOT / "build/semantic-python"
    if local.is_dir() and str(local) not in sys.path:
        sys.path.insert(0, str(local))
    try:
        import numpy
        return numpy
    except ImportError as error:
        raise SemanticUnavailable("Dependências semânticas ausentes. Execute tools/prepare_semantic.py.") from error


def normalize(vector):
    np = numpy_module()
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or not np.isfinite(norm) or norm < 1e-12:
        raise ValueError("Embedding inválido ou de norma zero")
    return vector / norm


class LocalEncoder:
    """Load lazily: listing the catalog never loads the neural model."""

    def __init__(self):
        self.session = None
        self.tokenizer = None

    def load(self):
        if self.session is not None:
            return
        numpy_module()
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            ort.disable_telemetry_events()
            folder = ROOT / "assets/models/minilm"
            if not (folder / "model.onnx").is_file() or not (folder / "tokenizer.json").is_file():
                raise SemanticUnavailable("Modelo local ausente. Execute tools/prepare_semantic.py.")
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
            tokenizer.enable_truncation(max_length=128)
            tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
            session = ort.InferenceSession(str(folder / "model.onnx"), options,
                                           providers=["CPUExecutionProvider"])
            self.tokenizer, self.session = tokenizer, session
        except Exception as error:
            if isinstance(error, (ImportError, OSError, RuntimeError, ValueError)) or type(error).__module__.startswith("onnxruntime"):
                raise SemanticUnavailable(f"Modelo semântico indisponível: {error}") from error
            raise

    def embedding_livro(self, text: str):
        parts = text_chunks(text)
        self.load()
        np = numpy_module()
        # Keep overflow tokens: 80 words can exceed the model's 128-token window.
        encoded = self.tokenizer.encode_batch(parts)
        windows = select_chunks([window for part in encoded for window in [part, *part.overflowing]])
        vectors = self._encode_windows(windows)
        return normalize(vectors.mean(axis=0)), len(windows)

    def encode_texts(self, texts):
        """Batch independent passages/keyphrases, retaining tokenizer overflows."""
        self.load()
        np = numpy_module()
        if not texts:
            return np.empty((0, DIMENSIONS), dtype=np.float32)
        encoded = self.tokenizer.encode_batch(texts)
        windows, owners = [], []
        for owner, part in enumerate(encoded):
            for window in [part, *part.overflowing]:
                windows.append(window)
                owners.append(owner)
        vectors = self._encode_windows(windows, trim_padding=True)
        totals = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        np.add.at(totals, owners, vectors)
        return np.stack([normalize(vector) for vector in totals])

    def _encode_windows(self, windows, trim_padding=False):
        np = numpy_module()
        vectors = []
        names = {item.name for item in self.session.get_inputs()}
        for offset in range(0, len(windows), 8):
            batch = windows[offset:offset + 8]
            lengths = [sum(item.attention_mask) if trim_padding else len(item.ids) for item in batch]
            width = max(lengths)
            ids = np.zeros((len(batch), width), dtype=np.int64)
            mask = np.zeros_like(ids)
            types = np.zeros_like(ids)
            for i, (item, length) in enumerate(zip(batch, lengths)):
                ids[i, :length] = item.ids[:length]
                mask[i, :length] = item.attention_mask[:length]
                types[i, :length] = item.type_ids[:length]
            inputs = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
            try:
                hidden = self.session.run(None, {key: value for key, value in inputs.items() if key in names})[0]
            except Exception as error:
                if type(error).__module__.startswith("onnxruntime"):
                    raise SemanticUnavailable(f"Falha na inferência semântica local: {error}") from error
                raise
            if hidden.ndim == 3:
                weights = mask[..., None].astype(np.float32)
                pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1)
            else:
                pooled = hidden
            for vector in pooled:
                vectors.append(normalize(vector))
        return np.stack(vectors)


ENCODER = LocalEncoder()
