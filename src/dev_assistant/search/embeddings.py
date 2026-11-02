"""Embedding provider interface and HuggingFace implementation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HuggingFaceEmbeddingProvider:
    """Local sentence-transformer embeddings; model files are downloaded by HF on first use."""

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        *,
        cache_folder: Path = Path("data/huggingface"),
    ) -> None:
        cache_folder.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_folder.resolve())
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
        except ImportError as exc:
            raise RuntimeError(
                "Install the optional AI dependencies with `uv sync --extra ai`."
            ) from exc
        self._embeddings = HuggingFaceEmbeddings(
            model_name=model_name,
            cache_folder=str(cache_folder / "hub"),
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embeddings.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embeddings.embed_query(text)
