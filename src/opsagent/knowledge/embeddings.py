"""Embedding functions for the policy index (DEC-008, DEC-021).

- "onnx": ChromaDB's default all-MiniLM-L6-v2 ONNX model (downloads ~80 MB on first use).
- "local": a small built-in hashed bag-of-words embedding. No download, no API, fully offline.
  Used for tests and as an automatic fallback when the ONNX model cannot be downloaded
  (e.g. restricted networks).
- "auto" (default): try "onnx", fall back to "local" if the download fails.

The backend actually used is stored in the collection metadata so queries always use the
same embedding as the index.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re

from chromadb import Documents, EmbeddingFunction, Embeddings

log = logging.getLogger("opsagent.embeddings")

DIM = 512
_STOP = set(
    "a an the of to in on for and or is are be by with within from at as it this that any "
    "if must may can not no will their they them its".split()
)


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def word_tokens(text: str) -> list[str]:
    """Lower-cased, stop-word-free, lightly stemmed words (shared with keyword search)."""
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]


def _tokens(text: str) -> list[str]:
    words = [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]


class LocalHashEmbedding(EmbeddingFunction[Documents]):
    """Deterministic hashed term-frequency embedding (sublinear tf, L2-normalized)."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "opsagent_local_hash"

    def get_config(self) -> dict:
        return {"dim": DIM}

    @staticmethod
    def build_from_config(config: dict) -> LocalHashEmbedding:
        return LocalHashEmbedding()

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002 (chroma signature)
        out = []
        for text in input:
            vec = [0.0] * DIM
            counts: dict[int, int] = {}
            for tok in _tokens(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                idx = h % DIM
                counts[idx] = counts.get(idx, 0) + (1 if (h >> 64) & 1 else -1)
            for idx, c in counts.items():
                vec[idx] = math.copysign(1 + math.log(abs(c)), c) if c else 0.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def onnx_embedding():
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    return DefaultEmbeddingFunction()


def choose(preferred: str | None = None) -> tuple[str, EmbeddingFunction]:
    """Return (backend_name, embedding_function) according to OPSAGENT_EMBEDDINGS."""
    mode = (preferred or os.getenv("OPSAGENT_EMBEDDINGS") or "auto").lower()
    if mode == "local":
        return "local", LocalHashEmbedding()
    ef = onnx_embedding()
    if mode == "onnx":
        return "onnx", ef
    try:
        ef(["warm up"])  # triggers the one-time model download
        return "onnx", ef
    except Exception as exc:  # network blocked, proxy, etc.
        log.warning("ONNX embedding model unavailable (%s); using local embeddings.", exc)
        return "local", LocalHashEmbedding()


def by_name(name: str) -> EmbeddingFunction:
    return LocalHashEmbedding() if name == "local" else onnx_embedding()
