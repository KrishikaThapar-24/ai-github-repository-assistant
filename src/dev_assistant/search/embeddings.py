"""Embedding providers for local and hosted repository indexing."""

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


class ChromaOnnxEmbeddingProvider:
    """Use Chroma's small ONNX MiniLM model without loading PyTorch."""

    def __init__(self) -> None:
        try:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "The Chroma ONNX embedding provider is unavailable. "
                "Install the project's base dependencies with `pip install .`."
            ) from exc
        self._embedding_function = ONNXMiniLM_L6_V2()

    # Chroma runs 32 texts through the model at once by default. Small slices keep the
    # transient memory low enough for 512 MB hosts at a small speed cost.
    _SLICE_SIZE = 8

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._SLICE_SIZE):
            for vector in self._embedding_function(texts[start : start + self._SLICE_SIZE]):
                vectors.append([float(value) for value in vector])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
