# BÁO CÁO ĐÁNH GIÁ CHIẾN LƯỢC DỮ LIỆU RAG (RAG DATA STRATEGY BENCHMARK REPORT)
**Tài liệu thử nghiệm (Corpus):** International Debt Report 2026 (World Bank Group)  
**Ngày thực hiện:** 13/11/2026 | **Phiên bản:** 1.0  

---

## 1. TỔNG QUAN HỆ THỐNG VÀ CHIẾN LƯỢC BẢO ĐỎM TÍNH TOÀN VẸN (IDEMPOTENCY & STALE PURGING)

Hệ thống RAG được thiết kế để so sánh **3 cấu hình chiến lược dữ liệu** trên cùng một corpus, model và bộ câu hỏi benchmark:

1. **Cấu hình A (Fixed-size Chunking):** Chia văn bản cố định theo kích thước 180-200 ký tự, độ gối (overlap) 30 ký tự.
2. **Cấu hình B (Paragraph-based / Semantic Chunking):** Chia văn bản theo đoạn văn và tiêu đề mục có ý nghĩa ngữ nghĩa đầy đủ.
3. **Cấu hình C (Paragraph-based + Metadata Filtering):** Sử dụng cùng cấu trúc chunk như B, nhưng tích hợp bộ lọc Metadata cứng (`year`, `income_group`, `region`, `category`, `section`) trước khi thực hiện truy xuất tương đồng. Khi câu hỏi không yêu cầu filter, cấu hình C tự động chuyển sang chế độ `unfiltered_mode: true`.

### Cơ chế chống nạp trùng & Xử lý Chunk cũ khi dữ liệu thay đổi:
- **Idempotency (Chống trùng lặp):** Mỗi chunk được tạo ra một mã băm MD5 duy nhất dựa trên `text` và `metadata`. Trước khi lưu vào Vector Store, hệ thống kiểm tra `chunk_registry`. Nếu MD5 hash đã tồn tại, chunk sẽ bị loại bỏ ngay lập tức.
- **Stale Chunk Purging (Xóa chunk lỗi thời):** Mỗi văn bản gốc sở hữu một `doc_id`. Khi nội dung văn bản gốc cập nhật, hệ thống kích hoạt hàm `purge_doc_chunks(doc_id)` để hủy toàn bộ các chunk cũ liên quan trước khi nạp phiên bản mới.

---

## 2. BỘ CÂU HỎI BENCHMARK (20 CÂU: 15 DEV / 5 TEST)

Bộ câu hỏi được xây dựng theo tiêu chuẩn `BENCHMARK.md`, đã phân chia cố định **15 câu Dev Set** (dùng tinh chỉnh hệ thống) và **5 câu Test Set** (chỉ mở ra kiểm thử độc lập một lần).

- **Số lượng:** 20 câu (15 Dev, 5 Test).
- **Phân bổ loại câu:** 5 câu Trực tiếp (Direct), 6 câu Diễn đạt lại (Paraphrased), 5 câu Điều kiện/Ngoại lệ (Conditional/Exception), 4 câu Lọc theo Metadata (Metadata Filter - 3 Dev, 1 Test).
- **Các trường bắt buộc:** `id`, `split`, `question_type`, `question`, `expected_answer`, `gold_source_id`, `gold_page`, `evidence_quote`, `filter`.

---

## 3. BÁO CÁO CHI TIẾT THEO 5 TIÊU CHÍ ĐÁNH GIÁ (BENCHMARK EVALUATION)

### TIÊU CHÍ 1: ĐỘ CHÍNH XÁC TRUY XUẤT (RETRIEVAL ACCURACY)

| Cấu hình | Dev Hit@1 (15 câu) | Dev Hit@3 (15 câu) | Test Hit@1 (5 câu) | Test Hit@3 (5 câu) | Tỷ lệ tuân thủ Filter (Filter Compliance) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Cấu hình A (Fixed-size)** | 14/15 (93.3%) | 15/15 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | N/A (Unfiltered) |
| **Cấu hình B (Paragraph)** | 14/15 (93.3%) | 15/15 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | N/A (Unfiltered) |
| **Cấu hình C (Paragraph + Filter)** | 14/15 (93.3%) | 15/15 (100.0%) | 5/5 (100.0%) | 5/5 (100.0%) | **4/4 (100.0%)** |

*Nhận xét:* Cả 3 cấu hình đều đạt tỷ lệ Hit@3 hoàn hảo 100% trên cả hai tập Dev và Test. Tuy nhiên, Cấu hình C đạt 100% tỷ lệ tuân thủ bộ lọc Metadata, loại bỏ hoàn toàn nhiễu từ các khu vực/nhóm thu nhập khác.

---

### TIÊU CHÍ 2: TÍNH MẠCH LẠC CỦA CHUNK (CHUNK COHERENCE)

Đánh giá thủ công trên 10 vị trí nguồn đại diện (thang điểm 0–2):
- **2 điểm:** Chunk giữ trọn vẹn câu, ý nghĩa hoàn chỉnh, không bị đứt đoạn.
- **1 điểm:** Chunk bị ngắt giữa câu hoặc tách rời số liệu khỏi ngữ cảnh.
- **0 điểm:** Chunk bị cắt vụn, mất hoàn toàn ngữ nghĩa.

| Vị trí nguồn (Source Position) | Cấu hình A (Fixed-size) | Cấu hình B & C (Paragraph) | Lý do chấm điểm / So sánh |
| :--- | :---: | :---: | :--- |
| **Pos 1: Total External Debt ($9.4T)** | 1 | 2 | Cấu hình A bị ngắt giữa chừng ở 200 ký tự; B giữ trọn đoạn Executive Summary. |
| **Pos 2: USD Depreciation (28.2%)** | 1 | 2 | A tách phần trăm 28.2% khỏi phần giải thích hiệu ứng định giá tỷ giá. |
| **Pos 3: MIC Debt Excl. China ($6.9T)** | 1 | 2 | A cắt ngang danh sách 10 nước MIC lớn nhất; B giữ trọn ngữ cảnh loại trừ Trung Quốc. |
| **Pos 4: IDA Interest Rate (8.2%)** | 1 | 2 | A tách mức lãi 8.2% khỏi mốc so sánh lịch sử 1989 và chênh lệch 5.5 điểm % với chủ nợ chính thức. |
| **Pos 5: LIC Debt Stock ($262.4B)** | 1 | 2 | A cắt rời số liệu LIC khỏi đợt tăng kỷ lục từ năm 2020. |
| **Pos 6: Net Debt Transfers ($45.4B)** | 1 | 2 | A chia làm 2 chunk làm mất sự so sánh giữa outflow 2024 và inflow 2025. |
| **Pos 7: Multilateral Excl. Ukraine** | 1 | 2 | A tách khoản loại trừ Ukraine (7.0%) khỏi tổng số 9.6%. |
| **Pos 8: IMF Argentina ($14.4B)** | 1 | 2 | A cắt số liệu $14.4B của Argentina rời khỏi tổng giải ngân $30.7B của IMF. |
| **Pos 9: Remittances vs FDI ($294.5B)** | 1 | 2 | A làm phân mảnh câu so sánh kiều hối và dòng FDI. |
| **Pos 10: Three-Tier Architecture** | 0 | 2 | A cắt nát định nghĩa Tier 1 (DRS), Tier 2 (IDS), Tier 3 (IDR); B giữ trọn cấu trúc 3 tầng. |
| **ĐIỂM TRUNG BÌNH COHERENCE** | **0.9 / 2.0 (45%)** | **2.0 / 2.0 (100%)** | **Cấu hình B/C vượt trội tuyệt đối về tính mạch lạc ngữ nghĩa.** |

---

### TIÊU CHÍ 3: TÍNH HỮU DỤNG CỦA METADATA (METADATA UTILITY)

1. **Truy vết nguồn (Traceability):** Trong kết quả Top-3 của tất cả các câu hỏi, toàn bộ các chunk đều ghi nhận đầy đủ `source_id`, `page` (trang PDF gốc), `section`, `year`, `income_group`, `region`, và `category`.
2. **Tuân thủ bộ lọc (Filter Compliance Rate):** Đạt **100% (4/4 câu hỏi filter)**. Cụ thể:
   - `IDR2026-DEV-005`: Lọc chuẩn `income_group = Low-Income Countries`.
   - `IDR2026-DEV-009`: Lọc chuẩn `category = External Financial Flows`.
   - `IDR2026-DEV-012`: Lọc chuẩn `region = Sub-Saharan Africa`.
   - `IDR2026-TEST-004`: Lọc chuẩn `region = Europe and Central Asia`.
3. **Giá trị loại bỏ nhiễu:** Khi áp dụng filter ở Cấu hình C, các kết quả tìm kiếm loại trừ hoàn toàn các đoạn văn thuộc khu vực địa lý hoặc nhóm thu nhập không liên quan, giúp giảm 100% nguy cơ nhầm lẫn chỉ số giữa các vùng.

---

### TIÊU CHÍ 4: CHẤT LƯỢNG THÔNG TIN NỀN (GROUNDING QUALITY / EVIDENCE SUFFICIENCY)

Đánh giá độ đầy đủ của bằng chứng trong Top-3 trên 5 câu Test Set (thang điểm 0–2):

| Mã câu hỏi Test | Cấu hình A | Cấu hình B | Cấu hình C | Nhận xét chi tiết |
| :--- | :---: | :---: | :---: | :--- |
| **TEST-001 (IDA Debt-to-GNI 41.2%)** | 1 | 2 | 2 | A lấy được số 41.2% nhưng cắt mất nguyên nhân tăng trưởng GNI (10.5%); B & C chứa đủ bằng chứng. |
| **TEST-002 (IDA Interest Gap 5.5%p)** | 1 | 2 | 2 | A thiếu mốc so sánh năm 2016 (3.4%p); B & C cung cấp đầy đủ chuỗi 10 năm. |
| **TEST-003 (Lao PDR Bond 11.25%)** | 2 | 2 | 2 | Cả 3 cấu hình đều lấy đúng tên nước Lao PDR, $300M và lãi suất 11.25%. |
| **TEST-004 (ECA Debt Service - Filter)** | 1 | 1 | 2 | A & B bị lẫn các vùng khác ở Top 2-3; C nhờ Filter chỉ trả về đúng duy nhất chunk Europe & Central Asia. |
| **TEST-005 (China Debt Service 30%)** | 1 | 2 | 2 | A lấy được tỷ lệ 30% nhưng mất số tiền tuyệt đối $419.8B; B & C đủ cả 2 số liệu. |
| **ĐIỂM GROUNDING TEST SET** | **6 / 10 (60%)** | **9 / 10 (90%)** | **10 / 10 (100%)** | **Cấu hình C đạt điểm Grounding tối đa nhờ kết hợp đoạn mạch lạc và Metadata Filter.** |

---

### TIÊU CHÍ 5: TÁC ĐỘNG CỦA CHIẾN LƯỢC DỮ LIỆU (DATA STRATEGY IMPACT)

| Tiêu chí so sánh | Cấu hình A (Fixed-size) | Cấu hình B (Paragraph) | Cấu hình C (Paragraph + Filter) |
| :--- | :--- | :--- | :--- |
| **Số lượng Chunk sinh ra** | **50 chunks** | **20 chunks** | **20 chunks** |
| **Chi phí lưu trữ & Indexing** | Cao gấp 2.5 lần | Tối ưu (giảm 60%) | Tối ưu (giảm 60%) |
| **Hiện tượng đứt đoạn thông tin** | Thường xuyên (Coherence 45%) | Không có (Coherence 100%) | Không có (Coherence 100%) |
| **Độ chính xác bằng chứng (Grounding)** | 60% | 90% | **100%** |
| **Khả năng chống nhiễu phạm vi** | Thấp | Trung bình | **Tuyệt đối (100% Compliance)** |

#### Đánh đổi & Phân tích lỗi (Trade-off & Error Analysis):
- **Cấu hình A (Fixed-size):** Phù hợp với văn bản thô không cấu trúc, nhưng gây tác hại nghiêm trọng khi làm vỡ các điều kiện ngoại lệ (ví dụ: tách câu "loại trừ Trung Quốc" khỏi số liệu tổng), dẫn tới mô hình LLM dễ trả lời sai hoặc bị hallucination.
- **Cấu hình B (Paragraph):** Giữ trọn vẹn ngữ cảnh kinh tế vĩ mô và bằng chứng đầy đủ, giảm 60% số lượng vector cần lưu trữ.
- **Cấu hình C (Paragraph + Metadata Filter):** **Là chiến lược tối ưu nhất.** Khi có yêu cầu lọc phạm vi (ví dụ theo quốc gia, khu vực, nhóm thu nhập, năm), bộ lọc Metadata ngăn chặn hoàn toàn việc truy xuất sai lệch.

---

## 4. KẾT LUẬN VÀ KHUYẾN NGHỊ

1. **Chiến lược được chọn:** Khuyên dùng **Cấu hình C (Paragraph-based Chunking kết hợp Metadata Filtering)** cho các tài liệu báo cáo kinh tế / tài chính như International Debt Report.
2. **Quy trình triển khai RAG:**
   - Sử dụng phương pháp phân đoạn theo ngữ nghĩa (Paragraph / Section-based) thay vì chia cố định ký tự.
   - Trích xuất tự động Metadata (`year`, `region`, `income_group`, `category`) lúc nạp tài liệu.
   - Thiết lập cơ chế băm MD5 chống nạp trùng và hàm Purge tự động cập nhật chunk khi tài liệu gốc thay đổi.
