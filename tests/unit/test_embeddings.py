import os
import sys
from pathlib import Path
from types import SimpleNamespace

from dev_assistant.search.embeddings import (
    ChromaOnnxEmbeddingProvider,
    HuggingFaceEmbeddingProvider,
)


def test_embedding_model_uses_configured_huggingface_home(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    class FakeHuggingFaceEmbeddings:
        def __init__(self, **kwargs: str) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langchain_huggingface",
        SimpleNamespace(HuggingFaceEmbeddings=FakeHuggingFaceEmbeddings),
    )
    monkeypatch.setenv("HF_HOME", "C:/previous/cache")

    HuggingFaceEmbeddingProvider(cache_folder=tmp_path)

    assert Path(os.environ["HF_HOME"]) == tmp_path.resolve()
    assert captured == {
        "model_name": "sentence-transformers/all-MiniLM-L6-v2",
        "cache_folder": str(tmp_path / "hub"),
    }


def test_chroma_onnx_provider_returns_plain_float_vectors(monkeypatch) -> None:
    import chromadb.utils.embedding_functions as functions

    class FakeOnnxEmbeddingFunction:
        def __call__(self, texts: list[str]) -> list[list[int]]:
            return [[1, 2] for _ in texts]

    monkeypatch.setattr(functions, "ONNXMiniLM_L6_V2", FakeOnnxEmbeddingFunction)
    provider = ChromaOnnxEmbeddingProvider()

    assert provider.embed_documents(["one", "two"]) == [[1.0, 2.0], [1.0, 2.0]]
    assert provider.embed_query("query") == [1.0, 2.0]


def test_chroma_onnx_provider_embeds_in_small_slices_and_keeps_order() -> None:
    from dev_assistant.search.embeddings import ChromaOnnxEmbeddingProvider

    calls: list[int] = []

    def fake_embedding_function(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return [[float(len(text))] for text in texts]

    provider = ChromaOnnxEmbeddingProvider.__new__(ChromaOnnxEmbeddingProvider)
    provider._embedding_function = fake_embedding_function

    texts = ["a" * size for size in range(1, 21)]
    vectors = provider.embed_documents(texts)

    assert calls == [8, 8, 4]
    assert vectors == [[float(size)] for size in range(1, 21)]
    assert provider.embed_query("abc") == [3.0]


def _openai_provider(handler, **kwargs):
    import httpx

    from dev_assistant.search.embeddings import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider(
        "sk-test",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_openai_provider_batches_and_restores_order() -> None:
    import json

    import httpx

    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk-test"
        inputs = json.loads(request.content)["input"]
        batch_sizes.append(len(inputs))
        rows = [{"index": i, "embedding": [float(len(text))]} for i, text in enumerate(inputs)]
        return httpx.Response(200, json={"data": list(reversed(rows))})  # out of order

    provider = _openai_provider(handler, batch_size=4)
    vectors = provider.embed_documents(["a" * n for n in range(1, 10)])

    assert batch_sizes == [4, 4, 1]
    assert vectors == [[float(n)] for n in range(1, 10)]
    assert provider.embed_query("abc") == [3.0]


def test_openai_provider_sends_non_empty_bounded_inputs() -> None:
    import json

    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        seen.extend(inputs)
        return httpx.Response(
            200, json={"data": [{"index": i, "embedding": [0.0]} for i in range(len(inputs))]}
        )

    _openai_provider(handler).embed_documents(["", "x" * 50_000])

    assert seen[0] == " "
    assert len(seen[1]) == 20_000


def test_openai_provider_retries_rate_limits_then_succeeds(monkeypatch) -> None:
    import httpx

    monkeypatch.setattr("dev_assistant.search.embeddings.time.sleep", lambda _: None)
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    assert _openai_provider(handler).embed_query("hello") == [1.0]
    assert attempts["count"] == 2


def test_openai_provider_reports_bad_key_and_quota_clearly(monkeypatch) -> None:
    import httpx
    import pytest

    monkeypatch.setattr("dev_assistant.search.embeddings.time.sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="rejected the API key"):
        _openai_provider(lambda request: httpx.Response(401)).embed_query("x")
    with pytest.raises(RuntimeError, match="rate limit or quota"):
        _openai_provider(lambda request: httpx.Response(429)).embed_query("x")


def test_search_service_factory_selects_openai_backend(tmp_path) -> None:
    import pytest
    from pydantic import SecretStr

    from dev_assistant.agents.factory import create_search_service
    from dev_assistant.core.config import Settings
    from dev_assistant.search.embeddings import OpenAIEmbeddingProvider

    paths = {
        "chroma_persist_directory": tmp_path / "chroma",
        "lexical_database_path": tmp_path / "lexical.sqlite3",
    }
    service = create_search_service(
        Settings(
            embedding_backend="openai", openai_api_key=SecretStr("sk-test"), _env_file=None, **paths
        )
    )
    assert isinstance(service.embeddings, OpenAIEmbeddingProvider)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_search_service(Settings(embedding_backend="openai", _env_file=None, **paths))
