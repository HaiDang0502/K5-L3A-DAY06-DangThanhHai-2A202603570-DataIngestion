# Báo cáo cá nhân — Day 6

## Dữ liệu và pipeline
- Chủ đề: International Debt Statistics 2026 (World Bank Open Knowledge Repository)
- Nguồn/quyền: PDF công khai, CC BY 3.0 IGO (cần xác nhận trang bản quyền)
- Số trang: xem manifest; quy tắc tính trang vật lý bắt đầu từ 1
- Chuẩn hóa: NFC, gộp khoảng trắng ngang, giữ `\n\n` làm ranh giới đoạn
- Embedding: TF-IDF (baseline) — thay bằng BGE-M3 1024 chiều khi chạy thật
- Vector store: in-memory TF-IDF (baseline) — thay bằng PostgreSQL + pgvector

## Cấu hình và benchmark
- **A** (fixed_size_200_30): chunk_size=200, overlap=30, filter=none. Giả thuyết: Cửa sổ nhỏ, chồng lấn vừa phải giữ được ngữ cảnh cục bộ, Hit@3 cao nhưng chunk có thể cắt giữa ý.
- **B** (paragraph_based): chunk_size=max_size, overlap=0, filter=none. Giả thuyết: Chunk theo đoạn giữ trọn ý, coherence cao hơn A, nhưng đoạn dài có thể làm loãng vector.
- **C** (paragraph_based_plus_filter): chunk_size=max_size, overlap=0, filter=year/section. Giả thuyết: Giống B nhưng áp metadata filter trước khi xếp hạng, kỳ vọng tăng độ chính xác cho câu có filter.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 0/15 | 0/15 | 3031 | 0.21s | 0 |
| A | test | 0/5 | 0/5 | 3031 | 0.21s | 0 |
| B | dev | 0/15 | 0/15 | 269 | 0.03s | 0 |
| B | test | 0/5 | 0/5 | 269 | 0.03s | 0 |
| C | dev | 0/15 | 0/15 | 269 | 0.02s | 0 |
| C | test | 0/5 | 0/5 | 269 | 0.02s | 0 |

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N hoặc N/A) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | TODO chấm thủ công 10 mẫu | TODO | TODO | TODO |
| B | TODO | TODO | TODO | TODO |
| C | TODO | TODO | TODO | TODO |

Đường dẫn trace: `artifacts/retrieval-traces.json`; nhãn thủ công: `artifacts/manual_audit.csv`.

## Phân tích và kết luận
- So sánh A–B và B–C: TODO dựa trên metrics và trace ở trên.
- Chiến lược chọn, đánh đổi, điều kiện áp dụng: TODO.
- Hạn chế: test nhỏ (≥5 câu), đo thời gian/chi phí chưa đầy đủ: TODO.
- Nếu làm lại: TODO.