# BÁO CÁO CÁ NHÂN — DAY 6: DATA INGESTION & RAG BENCHMARK EVALUATION
**Học viên:** Đặng Thanh Hải  
**Mã số sinh viên (MSSV):** 2A202603570  
**Lớp:** K5-L3A  
**Dự án:** `K5-L3A-DAY06-DangThanhHai-2A202603570-DataIngestion`  
**Ngày thực hiện:** 13/11/2026  

---

## 1. DỮ LIỆU VÀ PIPELINE

### 1.1 Thông tin Nguồn dữ liệu & Quy tắc đếm trang
- **Tên tài liệu gốc:** *International Debt Report 2026* (Ngân hàng Thế giới - World Bank Group).
- **File dữ liệu:** `9781464823558.pdf`.
- **Bản quyền & Giấy phép:** Open Access theo chính sách của World Bank (Creative Commons Attribution 3.0 IGO - CC BY 3.0 IGO).
- **Thống kê trang:**
  - Tổng số trang PDF: **280 trang**.
  - Số trang nội dung hợp lệ được trích xuất: **253 trang** (đáp ứng điều kiện $\ge 30$ trang theo `BENCHMARK.md`).
  - **Quy tắc đếm trang:** Chỉ tính các trang chứa văn bản thực tế $> 20$ ký tự, loại bỏ các trang bìa rỗng, mục lục rỗng và trang trang trí.
- **Cảnh báo từ Manifest cần khắc phục:** File `data/manifest.json` ghi nhận cảnh báo thiếu trường `accessed_at`. Cần bổ sung giá trị `"accessed_at": "2026-11-13"` vào manifest.

### 1.2 Mô hình Embedding & Lưu trữ Vector
- **Embedding Model:** `intfloat/multilingual-e5-small` (Sentence-Transformers).
  - Số chiều (Embedding dimension): **384 dimensions**.
  - Thiết bị chạy: **CPU** (`No device provided, using cpu`).
- **Vector Store:** Local Persistent Index / In-memory Cosine Similarity Index với hỗ trợ Metadata Filtering.
- **Metric đo tương đồng:** Cosine Similarity.
- **Lệnh thực thi pipeline:** `python -m src.run`

### 1.3 Cấu trúc Metadata
- **Các trường Metadata bắt buộc:** `document_id`, `source`, `page`, `year`, `income_group`, `region`, `category`, `section`.
- **Nguồn giá trị filter:** Được trích xuất từ tiêu đề chương, mục thống kê và bảng biểu của tài liệu gốc.

---

## 2. CẤU HÌNH VÀ BENCHMARK

### 2.1 Ba Cấu hình Chiến lược Dữ liệu (A / B / C)
- **Cấu hình A (Fixed-size Chunking):** Chia văn bản theo kích thước cố định 200 ký tự (overlap 30 ký tự). Sinh ra **1,099 chunks**.
- **Cấu hình B (Paragraph-based Chunking):** Chia văn bản theo đoạn văn ngữ nghĩa hoàn chỉnh. Sinh ra **750 chunks**.
- **Cấu hình C (Paragraph + Metadata Filter):** Sử dụng cấu trúc chunk theo đoạn như B (**750 chunks**, tái sử dụng embedding từ B với `embed 0.0s`), tích hợp bộ lọc Metadata cứng (`income_group`, `region`, `category`, `year`). Với các câu không có bộ lọc, C chạy ở chế độ `unfiltered_mode: true`.

### 2.2 Bộ Câu hỏi Benchmark & Phân chia Tập dữ liệu
- **Tổng số câu hỏi:** 20 câu hỏi được kiểm chứng trực tiếp từ tài liệu gốc.
- **Phân chia tập dữ liệu:** **15 câu Dev Set** / **5 câu Test Set** (chốt trước khi thực nghiệm).
- **Phân bổ bộ lọc Metadata:** Có 4 câu sử dụng bộ lọc Metadata (3 câu Dev: `DEV-005`, `DEV-009`, `DEV-012` và 1 câu Test: `TEST-004`).

### 2.3 Chiến lược Lựa chọn Trước khi Xem Tập Test
- **Chiến lược được chốt:** **Cấu hình C**.
- **Lý do lựa chọn:** Trên tập Dev, Cấu hình C đạt Dev Hit@1 cao nhất (**8/15 = 53.3%**) và Dev Hit@3 cao nhất (**10/15 = 66.7%**), đồng thời đảm bảo 100% tuân thủ bộ lọc Metadata để khoanh vùng dữ liệu chính xác.

---

## 3. KẾT QUẢ ĐÁNH GIÁ THỰC NGHIỆM

### 3.1 Bảng 1: Kết quả Định lượng (Quantitative Metrics)

| Chiến lược (Strategy) | Tập dữ liệu (Split) | Hit@1 (n/N & %) | Evidence Hit@3 (n/N & %) | Số lượng Chunk | Thời gian (Embed + Store) | Chi phí |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Config A (Fixed-size)** | Dev Set (15 câu) | 4/15 (26.7%) | 8/15 (53.3%) | 1,099 | 30.7s + 0.8s = 31.5s | 0 USD (CPU Local) |
| **Config A (Fixed-size)** | Test Set (5 câu) | 1/5 (20.0%) | 2/5 (40.0%) | 1,099 | - | 0 USD (CPU Local) |
| **Config B (Paragraph)** | Dev Set (15 câu) | 6/15 (40.0%) | 8/15 (53.3%) | 750 | 28.7s + 0.6s = 29.3s | 0 USD (CPU Local) |
| **Config B (Paragraph)** | Test Set (5 câu) | 3/5 (60.0%) | 4/5 (80.0%) | 750 | - | 0 USD (CPU Local) |
| **Config C (Paragraph + Filter)** | Dev Set (15 câu) | **8/15 (53.3%)** | **10/15 (66.7%)** | **750** | **0.0s + 0.6s = 0.6s** | **0 USD (CPU Local)** |
| **Config C (Paragraph + Filter)** | Test Set (5 câu) | **3/5 (60.0%)** | **4/5 (80.0%)** | **750** | **-** | **0 USD (CPU Local)** |

---

### 3.2 Bảng 2: Kết quả Định tính (Qualitative Assessment - 5 Tiêu chí)

| Chiến lược (Strategy) | Coherence trung bình /2 (10 vị trí) | Metadata đủ & đúng (n/N) | Tuân thủ Filter (n/N) | Grounding Score trung bình /2 (5 câu test) |
| :--- | :---: | :---: | :---: | :---: |
| **Config A (Fixed-size)** | 0.9 / 2.0 | 20/20 (100%) | N/A (Unfiltered) | 1.0 / 2.0 |
| **Config B (Paragraph)** | 1.8 / 2.0 | 20/20 (100%) | N/A (Unfiltered) | 1.6 / 2.0 |
| **Config C (Paragraph + Filter)** | **2.0 / 2.0** | **20/20 (100%)** | **4/4 (100%)** | **1.8 / 2.0** |

---

## 4. PHÂN TÍCH CHI TIẾT VÀ KẾT LUẬN

### 4.1 So sánh Chiến lược A – B và B – C
1. **So sánh A – B (Fixed-size vs Paragraph):**
   - Config B vượt trội hơn Config A ở cả chỉ số Hit@1 (Dev: 40.0% vs 26.7%; Test: 60.0% vs 20.0%) và Hit@3 (Test: 80.0% vs 40.0%).
   - **Nguyên nhân:** Config A bị vỡ câu khi cắt ở mốc 200 ký tự, làm tách rời các số liệu quan trọng khỏi bối cảnh chủ thể (ví dụ: số liệu lãi suất bị tách khỏi tên nhóm nước IDA). Config B giữ trọn vẹn ngữ cảnh của đoạn văn.
   - **Tài nguyên:** Config B giảm **31.7% số lượng chunk** (từ 1,099 xuống 750 chunks), giúp giảm thời gian embedding từ 30.7s xuống 28.7s.

2. **So sánh B – C (Paragraph vs Paragraph + Metadata Filter):**
   - Config C tiếp tục nâng Dev Hit@1 từ 40.0% lên **53.3%** và Dev Hit@3 từ 53.3% lên **66.7%**.
   - **Tác động của Filter:** Trên 4 câu hỏi có Metadata Filter, Config C đạt **100% độ tuân thủ (4/4)**, giúp loại bỏ hoàn toàn các chunk nhiễu đến từ các khu vực địa lý khác (như tách biệt chỉ số của *Sub-Saharan Africa* khỏi *Europe and Central Asia*).

---

### 4.2 Phân tích 2 Ví dụ Thất bại (Error Analysis)

1. **Ví dụ lỗi 1: `IDR2026-DEV-007` (Ngoại trừ khoản hỗ trợ Ukraine theo sáng kiến ERA)**
   - **Lỗi ở Config A & B:** Cả A và B đều trả về đoạn văn nói chung về nợ đa phương nhưng bị thiếu điều kiện loại trừ khoản hỗ trợ của EU cho Ukraine ($1.4 trillion vs $1.5 trillion).
   - **Nguyên nhân:** Từ khóa "Ukraine" và "ERA" xuất hiện ở phần chú thích cuối trang, khoảng cách vector trong không gian nhúng thuần túy (Dense Embedding) chưa đủ ưu tiên chú thích nhỏ này.

2. **Ví dụ lỗi 2: `IDR2026-TEST-003` (Đợt phát hành trái phiếu quốc tế của CHDCND Lào 11.25%)**
   - **Lỗi ở Config A:** Config A trả về chunk chứa con số lãi suất 11.25% ở Top-1 nhưng lại nằm ở đoạn văn cắt khúc thiếu mất tên quốc gia "Lao PDR".
   - **Khắc phục ở Config B & C:** Config B và C nhờ giữ trọn đoạn văn nên đã lấy lại đầy đủ cả con số 11.25% lẫn tên quốc gia Lao PDR.

---

### 4.3 Đánh đổi & Hạn chế
- **Đánh đổi:** Cấu hình C phụ thuộc vào việc người dùng hoặc hệ thống front-end phải cung cấp chính xác thuộc tính Metadata Filter trước khi truy xuất. Nếu bộ lọc bị truyền sai giá trị, kết quả truy xuất có thể bị rỗng.
- **Hạn chế của bài test:** Bộ câu hỏi benchmark hiện tại gồm 20 câu; quy mô thử nghiệm có thể mở rộng lên 50–100 câu để đánh giá sâu hơn.

---

### 4.4 Khuyến nghị Cải tiến (Next Steps)
1. **Bổ sung Hybrid Search (Dense + BM25):** Kết hợp mô hình `multilingual-e5-small` với giải thuật tìm kiếm từ khóa BM25 để tối ưu các câu hỏi chứa số liệu chính xác.
2. **Bổ sung Metadata tự động (Auto-Metadata Ingestion):** Tự động trích xuất các trường `income_group` và `region` từ tiêu đề và bảng biểu trong quá trình ingest dữ liệu.
3. **Cập nhật Manifest:** Thêm trường `"accessed_at": "2026-11-13"` vào `data/manifest.json`.

---

