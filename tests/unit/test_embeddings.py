import os
import sys
from pathlib import Path
from types import SimpleNamespace

from dev_assistant.search.embeddings import HuggingFaceEmbeddingProvider


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
