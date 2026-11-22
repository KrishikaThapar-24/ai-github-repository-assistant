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
