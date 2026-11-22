"""Embedding providers for local and hosted repository indexing."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx


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


class OpenAIEmbeddingProvider:
    """Hosted embeddings over HTTP: no local model, so it fits small (512 MB) servers."""

    _MAX_CHARS = 20_000  # keeps each text safely under the API's per-input token limit

    def __init__(
        self,
        api_key: str,
        model: str = "text-embedding-3-small",
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 60,
        batch_size: int = 64,
        max_retries: int = 2,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        if not api_key.strip():
            raise RuntimeError("The OpenAI embedding backend needs DEV_ASSISTANT_OPENAI_API_KEY.")
        self._api_key = api_key.strip()
        self._model = model
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=timeout_seconds))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        with self._client_factory() as client:
            for start in range(0, len(texts), self._batch_size):
                batch = [
                    (text[: self._MAX_CHARS] or " ") for text in texts[start : start + self._batch_size]
                ]
                vectors.extend(self._embed_batch(client, batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _embed_batch(self, client: httpx.Client, batch: list[str]) -> list[list[float]]:
        for attempt in range(self._max_retries + 1):
            try:
                response = client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self._model, "input": batch},
                )
            except httpx.HTTPError as exc:
                if attempt < self._max_retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"Could not reach the OpenAI embeddings API: {exc}") from exc
            if response.status_code in {429, 500, 502, 503, 504} and attempt < self._max_retries:
                time.sleep(2**attempt)
                continue
            break

        if response.status_code == 401:
            raise RuntimeError(
                "OpenAI rejected the API key used for embeddings. Check DEV_ASSISTANT_OPENAI_API_KEY."
            )
        if response.status_code == 429:
            raise RuntimeError(
                "OpenAI rate limit or quota reached for embeddings. Check the account's billing "
                "and usage limits."
            )
        if not response.is_success:
            raise RuntimeError(f"OpenAI embeddings returned HTTP {response.status_code}.")
        try:
            rows = sorted(response.json()["data"], key=lambda row: row["index"])
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("OpenAI returned an unreadable embeddings response.") from exc
        if len(vectors) != len(batch):
            raise RuntimeError("OpenAI returned the wrong number of embeddings.")
        return vectors
