"""Choose a real embedding provider (API or local) and implement this contract."""
from typing import Protocol

from sentence_transformers import SentenceTransformer


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Local embedding provider backed by BAAI/bge-m3."""

    def __init__(self, model_name: str = "BAAI/bge-m3") -> None:
        self.model_name = model_name
        # Chạy cục bộ, không phụ thuộc API bên ngoài
        self._model = SentenceTransformer(model_name)
        # bge-m3 dense vector là 1024 chiều
        self.dimensions = self._model.get_sentence_embedding_dimension()
        if self.dimensions != 1024:
            raise ValueError(
                f"Expected 1024 dimensions for bge-m3, got {self.dimensions}"
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # normalize_embeddings=True để cosine similarity ổn định hơn
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, question: str) -> list[float]:
        if not question or not question.strip():
            raise ValueError("question must be a non-empty string")
        vector = self._model.encode(
            question,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()