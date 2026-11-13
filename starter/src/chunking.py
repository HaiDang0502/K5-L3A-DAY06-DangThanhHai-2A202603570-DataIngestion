"""Implement fixed-size and structure-aware chunking."""
from .models import Chunk, DocumentPage


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split text into overlapping windows and retain page metadata."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be in [0, size)")

    text = page.text or ""
    chunks: list[Chunk] = []
    step = size - overlap
    start = 0
    idx = 0

    while start < len(text):
        end = min(start + size, len(text))
        window = text[start:end]
        if window.strip():
            chunks.append(
                Chunk(
                    text=window,
                    page_number=page.page_number,
                    chunk_index=idx,
                )
            )
            idx += 1
        if end == len(text):
            break
        start += step

    return chunks


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group paragraphs, split oversized paragraphs and retain metadata."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    text = page.text or ""
    # Tách theo dòng trống (một hoặc nhiều dòng trống liên tiếp)
    raw_paragraphs = [p.strip() for p in text.split("\n\n")]
    paragraphs = [p for p in raw_paragraphs if p]

    chunks: list[Chunk] = []
    buffer = ""
    idx = 0

    def flush(buf: str) -> None:
        nonlocal idx
        if buf.strip():
            chunks.append(
                Chunk(
                    text=buf.strip(),
                    page_number=page.page_number,
                    chunk_index=idx,
                )
            )
            idx += 1

    for para in paragraphs:
        if len(para) <= max_size:
            # Đoạn văn bình thường: gộp vào buffer nếu vẫn vừa max_size
            candidate = f"{buffer}\n\n{para}" if buffer else para
            if len(candidate) <= max_size:
                buffer = candidate
            else:
                flush(buffer)
                buffer = para
        else:
            # Đoạn văn quá lớn: xả buffer hiện tại trước, rồi cắt đoạn văn
            flush(buffer)
            buffer = ""
            start = 0
            while start < len(para):
                end = min(start + max_size, len(para))
                piece = para[start:end]
                if piece.strip():
                    flush(piece)
                if end == len(para):
                    break
                start = end

    flush(buffer)
    return chunks