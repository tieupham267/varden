# Kiến trúc Varden

Tài liệu giải thích tổng quan kiến trúc và cách hoạt động của Varden.

---

## Varden là gì?

**Varden** là một **sidecar tự động phân tích threat intelligence (tình báo mối đe dọa)** bằng AI, chạy cặp đôi với [Oksskolten](https://github.com/babarot/oksskolten) — một RSS reader chuyên thu thập bài viết bảo mật.

### Vấn đề mà Varden giải quyết

Đội SOC/Security hàng ngày phải đọc hàng trăm bài viết bảo mật từ nhiều nguồn RSS. Varden tự động hóa việc này bằng cách:

1. **Đọc bài viết** mà Oksskolten đã thu thập (từ DB SQLite, read-only)
2. **Dùng AI phân tích** từng bài, cho điểm **mức độ liên quan** (0-10) đến tech stack của công ty
3. **Gửi cảnh báo** qua Telegram/Slack khi phát hiện mối đe dọa quan trọng
4. **Gửi email digest** hàng ngày tổng hợp các bài đáng chú ý

Tóm lại, đây là một tool **tự động hóa SOC level 1** — thay vì analyst phải đọc từng bài RSS, Varden lọc và đánh giá giúp, chỉ gửi alert những gì thực sự liên quan đến hạ tầng của công ty.

---

## Kiến trúc tổng thể (Sidecar pattern)

```
RSS feeds → Oksskolten (thu thập / dedup / extract)
         → SQLite DB (shared volume, read-only)
         → Varden pipeline (AI analyze → score → alert)
         → Telegram / Slack / Email
```

**Nguyên tắc sidecar**: Varden **không bao giờ ghi vào DB của Oksskolten**. Nó duy trì DB riêng (`data/varden_state.db`) để tracking cursor và cache kết quả phân tích. Điều này giúp Varden không bị ảnh hưởng khi Oksskolten cập nhật schema.

---

## Pipeline flow

Mỗi cycle (mặc định 15 phút/lần) chạy qua 5 bước:

```
1. Đọc cursor (last processed article ID) từ state DB
2. Fetch bài mới từ Oksskolten SQLite (id > cursor)
3. Với mỗi bài: skip nếu đã analyzed → gọi AI → lưu kết quả
4. Nếu relevance >= threshold VÀ severity khớp → gửi alert
5. Cập nhật cursor. Delay 0.5s giữa các bài (rate limiting)
```

### Logic gửi cảnh báo

Cảnh báo chỉ được gửi khi **cả hai điều kiện** thoả mãn (AND logic):

- `relevance_score >= ALERT_THRESHOLD` (mặc định 7/10)
- `severity` nằm trong `ALERT_SEVERITIES` (mặc định: critical, high)

Ví dụ: một bài critical nhưng relevance chỉ 5/10 (không liên quan đến công ty) sẽ **không** bắn alert.

---

## Các module chính

### `main.py` — CLI entry point

4 lệnh chính:

| Lệnh | Chức năng |
|-------|-----------|
| `python main.py run` | Chạy 1 cycle rồi thoát (dùng để test) |
| `python main.py daemon` | Chạy scheduled polling (production) |
| `python main.py digest` | Gửi email digest 24h gần nhất |
| `python main.py status` | Hiển thị state hiện tại và analyses gần đây |

Ở daemon mode, sử dụng APScheduler để poll mỗi N phút. Nếu email được bật, tự động gửi daily digest lúc 08:00 UTC (15:00 VN).

### `src/pipeline.py` — Orchestrator

~127 dòng, điều phối toàn bộ flow:

1. Đọc cursor từ state DB
2. Fetch bài mới từ Oksskolten
3. Lặp qua từng bài: skip nếu đã analyzed → gọi AI → lưu kết quả
4. Kiểm tra ngưỡng alert → dispatch nếu đủ điều kiện
5. Cập nhật cursor

Theo dõi stats mỗi cycle: `analyzed`, `skipped`, `failed`, `alerted`.

### `src/source.py` — Oksskolten data source

Đọc bài viết từ Oksskolten qua 2 mode:

- **SQLite mode** (khuyến nghị): Đọc trực tiếp DB qua shared volume, mở read-only (`?mode=ro`). Có **auto-schema discovery** — tự dò tên bảng và cột để tương thích nhiều phiên bản Oksskolten.
- **API mode** (fallback): Gọi REST API của Oksskolten, thử nhiều endpoint pattern phổ biến.

Output được normalize thành format thống nhất:

```python
{
    "id": int,
    "title": str,
    "url": str,
    "content": str,        # cắt tối đa 10,000 chars
    "published_at": str,
    "feed_name": str,
    "language": str,
}
```

Bài viết dưới 50 ký tự bị skip (chưa extract xong).

### `src/ai_analyzer.py` — AI phân tích

Hỗ trợ **13 provider** qua module `src/ai_providers.py`:

| Nhóm | Provider | `AI_PROVIDER` |
|------|----------|---------------|
| Native API | Anthropic Claude | `anthropic` |
| Native API | Google Gemini | `gemini` |
| Native API | Azure OpenAI | `azure-openai` |
| OpenAI-compatible | OpenAI, DeepSeek, Mistral, Groq, Together, Fireworks, xAI, OpenRouter, Ollama | `openai`, `deepseek`, `mistral`, `groq`, `together`, `fireworks`, `xai`, `openrouter`, `ollama` |
| Catch-all | Bất kỳ API tương thích OpenAI | `openai-compatible` |

Chi tiết cấu hình từng provider xem [configuration.md](configuration.md).

**Cách hoạt động:**

1. Load `config/company_profile.yaml` → build context text chứa tech stack, threat actors, MITRE techniques
2. Dựng system prompt bằng tiếng Việt, nhúng company context vào
3. Gọi LLM với nội dung bài viết (cắt 8,000 chars)
4. Parse JSON response → normalize và validate fields

**Output JSON:**

```json
{
  "relevance_score": 0-10,
  "relevance_reason": "Tại sao cho điểm này",
  "severity": "critical|high|medium|low|info",
  "summary_vi": "Tóm tắt tiếng Việt 2-4 câu",
  "cve_ids": ["CVE-YYYY-NNNNN"],
  "affected_products": ["product1"],
  "threat_actors": ["actor1"],
  "mitre_attack": [{"tactic": "TA00xx - Name", "technique": "Txxxx - Name"}],
  "recommendations": ["action 1", "action 2"]
}
```

### `src/state.py` — State management

SQLite async (aiosqlite), lưu trong `data/varden_state.db`:

| Table | Chức năng |
|-------|-----------|
| `cursor` | Vị trí đọc cuối cùng (last processed article ID) |
| `analyzed_articles` | Cache kết quả phân tích: severity, relevance_score, summary_vi, full JSON |

Chức năng chính:
- **Cursor tracking**: biết bài nào đã xử lý, poll incremental
- **Analysis cache**: không phân tích lại bài đã xử lý (tiết kiệm token)
- **Alert dedup**: tracking bài nào đã gửi alert, qua channel nào

### `src/notifier.py` — Gửi thông báo

3 kênh notification, có thể bật đồng thời:

| Kênh | Format | Thời gian |
|------|--------|-----------|
| Telegram | HTML với emoji, score bar (`●●●●●●●○○○`) | Real-time |
| Slack | Block Kit với color-coded attachments | Real-time |
| Email | HTML digest với border-left color-coded | Daily (08:00 UTC) |

Mỗi alert hiển thị: severity, relevance score, tóm tắt tiếng Việt, lý do liên quan, CVE, affected products, threat actors, MITRE techniques, và khuyến nghị cho SOC team.

---

## Company Profile — bộ não scoring

File `config/company_profile.yaml` là **lever chính** để tune chất lượng phân tích. AI sẽ so sánh nội dung bài viết với profile này để cho điểm relevance.

| Section | Mô tả | Ảnh hưởng |
|---------|--------|-----------|
| `tech_stack` | 10+ danh mục sản phẩm/vendor đang dùng | Bài viết match tech → score cao |
| `watched_threat_actors` | Threat actors cần theo dõi (Lazarus, APT32...) | Bài viết mention actor → score cao |
| `priority_techniques` | MITRE ATT&CK techniques ưu tiên | Bài viết match technique → score cao |
| `boost_keywords` | Từ khóa tăng điểm (Vietnam, banking...) | Tăng relevance khi xuất hiện |
| `reduce_keywords` | Từ khóa giảm điểm (crypto mining, gaming...) | Giảm relevance khi xuất hiện |

**Ví dụ**: Bài viết về lỗ hổng Fortinet FortiGate sẽ được điểm cao (vì nằm trong `tech_stack.network_security`), trong khi bài về gaming industry sẽ bị giảm điểm (nằm trong `reduce_keywords`).

---

## Tech stack

- **Python 3.12**, fully async (`asyncio`, `aiosqlite`, `httpx`)
- **APScheduler** cho daemon mode (interval + cron scheduling)
- **Jinja2** cho email templates
- **No web framework** — CLI-only entry point qua `main.py`
- **Docker** deployment với named volume sharing giữa Oksskolten và Varden

---

## Cách chạy

### Development

```bash
pip install -r requirements.txt
python main.py run          # Chạy 1 cycle (testing)
python main.py status       # Xem state hiện tại
```

### Production (Docker)

```bash
docker compose up -d
docker compose run --rm sidecar run       # Test 1 cycle
docker compose run --rm sidecar status    # Check state
docker compose logs -f varden             # Monitor logs
```
