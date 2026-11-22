"""Production graph assembly from validated settings."""

from dev_assistant.agents.answering import LangChainAnswerer, create_chat_model
from dev_assistant.agents.graphs.code_qa import CodeQAGraph
from dev_assistant.code_intel.dependencies import SQLiteDependencyIndex
from dev_assistant.core.config import Settings
from dev_assistant.github.factory import create_activity_client
from dev_assistant.search.embeddings import (
    ChromaOnnxEmbeddingProvider,
    HuggingFaceEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from dev_assistant.search.lexical_index import SQLiteLexicalIndex
from dev_assistant.search.service import CodeSearchService
from dev_assistant.search.vector_store import ChromaCodeStore


def create_search_service(settings: Settings) -> CodeSearchService:
    if settings.embedding_backend == "huggingface":
        embeddings = HuggingFaceEmbeddingProvider(
            settings.embedding_model_name,
            cache_folder=settings.embedding_cache_directory,
        )
    elif settings.embedding_backend == "openai":
        if settings.openai_api_key is None:
            raise RuntimeError(
                "DEV_ASSISTANT_EMBEDDING_BACKEND=openai needs DEV_ASSISTANT_OPENAI_API_KEY."
            )
        embeddings = OpenAIEmbeddingProvider(
            settings.openai_api_key.get_secret_value(), settings.openai_embedding_model
        )
    else:
        embeddings = ChromaOnnxEmbeddingProvider()
    return CodeSearchService(
        embeddings=embeddings,
        vectors=ChromaCodeStore(settings.chroma_persist_directory),
        lexical=SQLiteLexicalIndex(settings.lexical_database_path),
    )


def create_code_qa_graph(
    settings: Settings, search_service: CodeSearchService | None = None
) -> CodeQAGraph:
    search = search_service or create_search_service(settings)
    return CodeQAGraph(
        search,
        LangChainAnswerer(create_chat_model(settings)),
        activity=create_activity_client(settings),
        dependencies=SQLiteDependencyIndex(settings.dependency_database_path),
    )
