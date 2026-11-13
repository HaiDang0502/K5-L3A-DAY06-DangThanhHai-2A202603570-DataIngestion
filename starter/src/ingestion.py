"""Read and normalize source data."""
from pathlib import Path
import unicodedata
import re

from pypdf import PdfReader

from .models import DocumentPage


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract each physical page; keep its page number and source.

    Detect and record pages with no text. OCR, when needed, is a separate step.
    """
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    reader = PdfReader(str(path))
    pages: list[DocumentPage] = []

    for page_index, pdf_page in enumerate(reader.pages):
        # Số trang vật lý bắt đầu từ 1, khớp với cách người dùng đếm trang
        page_number = page_index + 1
        try:
            raw_text = pdf_page.extract_text() or ""
        except Exception as exc:
            # Không chặn toàn bộ tài liệu chỉ vì một trang lỗi;
            # ghi nhận là trang rỗng để OCR xử lý riêng sau này.
            raw_text = ""
            _ = exc

        text = normalize_text(raw_text)
        has_text = bool(text.strip())

        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page_number=page_number,
                text=text,
                has_text=has_text,
                char_count=len(text),
            )
        )

    return pages


_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Normalize Unicode and whitespace without discarding useful structure."""
    if not text:
        return ""

    # 1. Chuẩn hóa Unicode: NFC gộp dấu tiếng Việt thành ký tự dựng sẵn,
    #    giúp so khớp và lưu trữ ổn định (ví dụ "ế" luôn là một codepoint).
    text = unicodedata.normalize("NFC", text)

    # 2. Loại bỏ ký tự điều khiển không phải xuống dòng / tab
    text = "".join(
        ch for ch in text
        if ch in ("\n", "\t") or unicodedata.category(ch)[0] != "C"
    )

    # 3. Chuẩn hóa xuống dòng kiểu Windows/Mac về "\n"
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 4. Gộp khoảng trắng ngang (space, tab, NBSP, en/em space...) thành một space
    text = _WHITESPACE_RE.sub(" ", text)

    # 5. Bỏ khoảng trắng thừa ở đầu/cuối mỗi dòng, giữ cấu trúc dòng
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # 6. Gộp nhiều dòng trống liên tiếp thành tối đa hai (giữ ranh giới đoạn văn)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)

    return text.strip()