"""Store and search vectors using PostgreSQL + pgvector."""
from typing import Any
import psycopg
from pgvector.psycopg import register_vector

from .models import Chunk


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int) -> None:
        """Retain connection settings; validate model dimensions."""
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.database_url)
        # Bật extension và đăng ký kiểu vector cho kết nối
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        return conn

    def create_schema(self) -> None:
        """Create table with vector column and source/page/filter metadata."""
        with self._connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id    TEXT PRIMARY KEY,
                    strategy    TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    source      TEXT,
                    page        INTEGER,
                    metadata    JSONB DEFAULT '{{}}'::jsonb,
                    embedding   vector({self.dimensions})
                )
                """
            )
            # Index cho lọc theo strategy và metadata (filter)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(strategy)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_metadata ON chunks USING GIN(metadata)"
            )
            # HNSW index cho cosine distance
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_embedding
                ON chunks USING hnsw (embedding vector_cosine_ops)
                """
            )
            conn.commit()

    def upsert(
        self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]
    ) -> None:
        """Validate lengths/dimensions and write stable IDs without duplicates.

        When re-ingesting changed source documents, remove obsolete chunks too.
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must have the same length")

        for vec in vectors:
            if len(vec) != self.dimensions:
                raise ValueError(
                    f"vector dimension {len(vec)} != expected {self.dimensions}"
                )

        if not chunks:
            return

        with self._connect() as conn:
            # Xóa các chunk cũ của cùng strategy để tránh tồn đọng dữ liệu lỗi thời.
            # Có thể thu hẹp phạm vi xóa theo source nếu cần.
            conn.execute(
                "DELETE FROM chunks WHERE strategy = %s", (strategy,)
            )

            rows = []
            for chunk, vec in zip(chunks, vectors):
                rows.append(
                    (
                        chunk.chunk_id,
                        strategy,
                        chunk.text,
                        getattr(chunk, "source", None),
                        _page_number(chunk),
                        _metadata_json(chunk),
                        vec,
                    )
                )

            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO chunks
                        (chunk_id, strategy, text, source, page, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (chunk_id) DO UPDATE SET
                        strategy  = EXCLUDED.strategy,
                        text      = EXCLUDED.text,
                        source    = EXCLUDED.source,
                        page      = EXCLUDED.page,
                        metadata  = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding
                    """,
                    rows,
                )
            conn.commit()

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by cosine distance, return top-k.

        Return chunk ID, text, score and metadata. Parameterize user-supplied values.
        """
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"query vector dimension {len(query_vector)} != expected {self.dimensions}"
            )

        # Xây dựng điều kiện WHERE: strategy luôn cố định, filters chỉ cho phép
        # các key nằm trong metadata JSONB (không cho phép SQL injection qua key).
        where_parts = ["strategy = %s"]
        params: list[Any] = [strategy, query_vector]

        if filters:
            for key, value in filters.items():
                if not key.isidentifier():
                    raise ValueError(f"invalid filter key: {key!r}")
                where_parts.append("metadata ->> %s = %s")
                params.append(key)
                params.append(str(value))

        where_sql = " AND ".join(where_parts)

        sql = f"""
            SELECT
                chunk_id,
                text,
                source,
                page,
                metadata,
                1 - (embedding <=> %s) AS score
            FROM chunks
            WHERE {where_sql}
            ORDER BY embedding <=> %s
            LIMIT %s
        """

        # Thứ tự params: SELECT score, WHERE, ORDER BY, LIMIT
        # Cần chèn query_vector cho cả SELECT và ORDER BY
        final_params = [query_vector] + params[1:] + [query_vector, top_k]

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, final_params)
                rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "chunk_id": row[0],
                    "text": row[1],
                    "source": row[2],
                    "page": row[3],
                    "metadata": row[4],
                    "score": float(row[5]),
                }
            )
        return results


def _page_number(chunk: Chunk) -> int | None:
    page = getattr(chunk, "page", None)
    if isinstance(page, int):
        return page
    if isinstance(page, str):
        # Chấp nhận dạng "Page 12" từ run.py
        digits = "".join(ch for ch in page if ch.isdigit())
        return int(digits) if digits else None
    return None


def _metadata_json(chunk: Chunk) -> str:
    import json

    meta = getattr(chunk, "metadata", None) or {}
    return json.dumps(meta, ensure_ascii=False)