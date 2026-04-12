# Varden Configuration Guide

Hướng dẫn chi tiết cấu hình file `.env` cho Varden.

File `.env` là nơi chứa **mọi setting** của Varden: data source, AI provider, notification channels, và alert thresholds. Copy từ `.env.example`:

```bash
cp .env.example .env
```

Sau đó sửa theo các section bên dưới.

---

## 1. Data Source — nguồn đọc articles từ Oksskolten

```bash
OKSSKOLTEN_MODE=sqlite
OKSSKOLTEN_DB_PATH=/oksskolten-data/oksskolten.db
```

**Giải thích:**

| Biến | Mô tả | Giá trị đề xuất |
|---|---|---|
| `OKSSKOLTEN_MODE` | Mode đọc data từ Oksskolten | `sqlite` (khuyến nghị) |
| `OKSSKOLTEN_DB_PATH` | Đường dẫn DB của Oksskolten trong container Varden | `/oksskolten-data/oksskolten.db` |

**Khi nào dùng mode khác:**

- `sqlite`: Đọc SQLite trực tiếp qua shared volume. Nhanh, reliable, không phụ thuộc API docs. **Luôn nên dùng cái này.**
- `api`: Fallback khi không thể share volume. Phải config thêm:

```bash
# OKSSKOLTEN_MODE=api
# OKSSKOLTEN_API_URL=http://oksskolten:3000
# OKSSKOLTEN_API_KEY=optional-if-auth-enabled
```

---

## 2. AI Provider — chọn LLM dùng để phân tích

Varden hỗ trợ **13 provider**. Chỉ chọn 1, comment các phần còn lại.

### Tổng quan provider

| Provider | `AI_PROVIDER` | Chi phí ước tính (100 bài/ngày) | Ghi chú |
|----------|---------------|----------------------------------|---------|
| Anthropic Claude | `anthropic` | ~$0.30-0.60 | Chất lượng cao nhất |
| Google Gemini | `gemini` | ~$0.05-0.15 | Nhanh, giá tốt |
| Azure OpenAI | `azure-openai` | Theo Azure pricing | Enterprise, data residency |
| OpenAI | `openai` | ~$0.15-0.40 | GPT-4o, GPT-4o-mini |
| DeepSeek | `deepseek` | ~$0.03-0.08 | Rẻ nhất, server Trung Quốc |
| Mistral AI | `mistral` | ~$0.10-0.25 | EU-based, GDPR compliant |
| Groq | `groq` | Free tier có sẵn | Inference cực nhanh |
| Together AI | `together` | ~$0.05-0.15 | Multi-model, giá cạnh tranh |
| Fireworks AI | `fireworks` | ~$0.05-0.15 | Nhanh, nhiều model |
| xAI (Grok) | `xai` | ~$0.10-0.30 | Grok-3 |
| OpenRouter | `openrouter` | Tuỳ model chọn | 1 key, 200+ models |
| Ollama | `ollama` | **Free** (local) | Self-hosted, cần GPU |
| Generic | `openai-compatible` | Tuỳ provider | Bất kỳ API tương thích OpenAI |

### Option A: Anthropic Claude (chất lượng cao nhất)

```bash
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx
ANTHROPIC_MODEL=claude-sonnet-4-20250514
```

- **Model khuyến nghị**: `claude-sonnet-4-20250514` (cân bằng giá/chất lượng)
- **Lấy API key**: https://console.anthropic.com/settings/keys

### Option B: Google Gemini

```bash
AI_PROVIDER=gemini
GEMINI_API_KEY=AIzaSy-xxxxxxxxxxxxx
GEMINI_MODEL=gemini-2.5-flash
```

- **Model khuyến nghị**: `gemini-2.5-flash` (nhanh, rẻ) hoặc `gemini-2.5-pro` (chất lượng cao hơn)
- **Lấy API key**: https://aistudio.google.com/apikey
- Không cần thêm SDK — Varden gọi REST API trực tiếp

### Option C: Azure OpenAI (enterprise)

```bash
AI_PROVIDER=azure-openai
AZURE_OPENAI_API_KEY=xxxxxxxxxxxxx
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your-deployment-name
AZURE_OPENAI_API_VERSION=2024-10-21
```

- Phù hợp cho tổ chức đã có Azure subscription
- Data ở trong Azure region bạn chọn (data residency)
- Cần tạo resource và deployment trước trong Azure Portal

### Option D: OpenAI

```bash
AI_PROVIDER=openai
OPENAI_API_KEY=sk-xxxxxxxxxxxxx
OPENAI_MODEL=gpt-4o-mini
```

- **Model khuyến nghị**: `gpt-4o-mini` (rẻ, nhanh) hoặc `gpt-4o` (chất lượng cao)
- **Lấy API key**: https://platform.openai.com/api-keys

### Option E: DeepSeek (rẻ nhất)

```bash
AI_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxx
DEEPSEEK_MODEL=deepseek-chat
```

- **Model**: `deepseek-chat` (general) hoặc `deepseek-reasoner` (phân tích sâu hơn, chậm hơn)
- **Rẻ hơn Claude ~10 lần**
- **Lấy API key**: https://platform.deepseek.com/api_keys
- **Lưu ý**: Data gửi sang server Trung Quốc. Nếu có compliance concerns, cân nhắc.

### Option F: Mistral AI

```bash
AI_PROVIDER=mistral
MISTRAL_API_KEY=xxxxxxxxxxxxx
MISTRAL_MODEL=mistral-large-latest
```

- **Model**: `mistral-large-latest` (mạnh nhất) hoặc `mistral-small-latest` (rẻ, nhanh)
- **Lấy API key**: https://console.mistral.ai/api-keys
- Server EU — phù hợp nếu cần GDPR compliance

### Option G: Groq (nhanh nhất)

```bash
AI_PROVIDER=groq
GROQ_API_KEY=gsk_xxxxxxxxxxxxx
GROQ_MODEL=llama-3.3-70b-versatile
```

- **Inference cực nhanh** (LPU hardware)
- Có free tier — phù hợp để test
- **Lấy API key**: https://console.groq.com/keys
- Model: `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`

### Option H: Together AI

```bash
AI_PROVIDER=together
TOGETHER_API_KEY=xxxxxxxxxxxxx
TOGETHER_MODEL=meta-llama/Llama-3.3-70B-Instruct-Turbo
```

- Nhiều model open-source, giá cạnh tranh
- **Lấy API key**: https://api.together.ai/settings/api-keys

### Option I: Fireworks AI

```bash
AI_PROVIDER=fireworks
FIREWORKS_API_KEY=xxxxxxxxxxxxx
FIREWORKS_MODEL=accounts/fireworks/models/llama-v3p3-70b-instruct
```

- Inference nhanh, nhiều model open-source
- **Lấy API key**: https://fireworks.ai/account/api-keys

### Option J: xAI (Grok)

```bash
AI_PROVIDER=xai
XAI_API_KEY=xai-xxxxxxxxxxxxx
XAI_MODEL=grok-3-mini
```

- **Model**: `grok-3-mini` (nhanh, rẻ) hoặc `grok-3` (mạnh nhất)
- **Lấy API key**: https://console.x.ai

### Option K: OpenRouter (multi-model gateway)

```bash
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxx
OPENROUTER_MODEL=anthropic/claude-sonnet-4
```

- **1 API key, truy cập 200+ models** từ nhiều provider
- Dễ switch model mà không cần đổi key
- **Lấy API key**: https://openrouter.ai/keys
- Ví dụ model: `anthropic/claude-sonnet-4`, `google/gemini-2.5-flash`, `deepseek/deepseek-chat`

### Option L: Ollama (local, miễn phí)

```bash
AI_PROVIDER=ollama
OLLAMA_MODEL=llama3.3
OLLAMA_BASE_URL=http://localhost:11434/v1
```

- **Hoàn toàn miễn phí**, chạy trên máy local
- Cần GPU (hoặc CPU mạnh) để chạy model
- Cài đặt: https://ollama.com
- Pull model: `ollama pull llama3.3`
- Trong Docker: đổi `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`

### Option M: Generic OpenAI-compatible

```bash
AI_PROVIDER=openai-compatible
OPENAI_API_KEY=xxxxxxxxxxxxx
OPENAI_MODEL=model-name
OPENAI_BASE_URL=https://your-provider.com/v1
```

Dùng cho bất kỳ provider nào có endpoint `/chat/completions` tương thích OpenAI mà chưa có preset sẵn.

---

## 3. Alert Thresholds — điều kiện bắn alert

**Đây là phần bạn chỉnh nhiều nhất để tune noise.**

```bash
ALERT_THRESHOLD=7
ALERT_SEVERITIES=critical,high
```

### `ALERT_THRESHOLD` (0-10)

Ngưỡng relevance score — bài phải có score >= giá trị này mới được alert.

| Giá trị | Ý nghĩa | Khi nào dùng |
|---|---|---|
| `9` | Chỉ bài CỰC KỲ liên quan (exact match tech stack) | Muốn zero noise |
| `8` | Liên quan rất rõ ràng | **Khuyến nghị cho production** |
| `7` | Liên quan (mặc định) | Balance coverage và noise |
| `6` | Liên quan gián tiếp (sector/geography) | Paranoid mode |
| `5` | Tin tức chung trong ngành | Quá ồn |

### `ALERT_SEVERITIES`

Whitelist các severity được phép alert. Phân cách dấu phẩy, **không có space**.

**Các giá trị hợp lệ**: `critical`, `high`, `medium`, `low`, `info`

### Preset configs phổ biến

**Preset 1: Chỉ Critical (siêu strict, 0-2 alert/ngày)**

```bash
ALERT_THRESHOLD=7
ALERT_SEVERITIES=critical
```

Phù hợp nếu bạn là CISO hoặc Security Manager chỉ muốn biết khi có incident nghiêm trọng.

**Preset 2: Balanced (khuyến nghị, 5-10 alert/ngày)**

```bash
ALERT_THRESHOLD=7
ALERT_SEVERITIES=critical,high
```

Phù hợp cho SOC analyst cần nắm các vuln/threat đáng chú ý.

**Preset 3: Comprehensive (15-30 alert/ngày)**

```bash
ALERT_THRESHOLD=6
ALERT_SEVERITIES=critical,high,medium
```

Phù hợp cho threat intel analyst cần coverage rộng.

### Logic lọc

Varden dùng AND giữa 2 điều kiện:

```
alert_này_được_bắn = (relevance_score >= ALERT_THRESHOLD)
                  AND (severity IN ALERT_SEVERITIES)
```

Nghĩa là bài phải **vừa đủ relevance vừa đúng severity** mới được alert. Một bài critical nhưng relevance chỉ 5 (không liên quan đến công ty) sẽ KHÔNG bắn alert.

---

## 4. Scheduler — lịch chạy pipeline

```bash
POLL_INTERVAL_MINUTES=15
BATCH_SIZE=20
```

| Biến | Mô tả | Đề xuất |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | Mỗi bao nhiêu phút chạy 1 cycle | `15` |
| `BATCH_SIZE` | Số article tối đa xử lý mỗi cycle (cost control) | `20` |

**Tuning:**

- **Muốn nhanh hơn?** Hạ xuống 10 phút — nhưng chú ý rate limit AI provider.
- **Muốn tiết kiệm token?** Tăng interval lên 30-60 phút, giảm batch xuống 10.
- **Nhiều feeds mới?** Tăng batch size lên 50, nhưng theo dõi cost.

---

## 5. Notification Channels

Có thể bật nhiều channel cùng lúc. Alert sẽ được gửi đến tất cả channels đang bật.

### Telegram (khuyến nghị — miễn phí, nhanh nhất)

```bash
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
```

**Setup:**

1. Chat với [@BotFather](https://t.me/botfather) → `/newbot` → đặt tên → nhận `TELEGRAM_BOT_TOKEN`
2. Tạo group (hoặc dùng private chat) → add bot vào group
3. Gửi 1 tin nhắn vào group
4. Lấy `chat_id`:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
   Trong response tìm `"chat":{"id":-1001234567890,...}` → đó là `TELEGRAM_CHAT_ID`
5. Lưu ý: ID group bắt đầu bằng dấu `-`

### Slack

```bash
SLACK_ENABLED=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/HERE
```

**Setup:**

1. Vào https://api.slack.com/apps → **Create New App** → **From scratch**
2. Đặt tên "Varden Alerts" → chọn workspace
3. Menu bên trái: **Incoming Webhooks** → bật **ON**
4. **Add New Webhook to Workspace** → chọn channel (ví dụ `#security-alerts`)
5. Copy webhook URL → paste vào `SLACK_WEBHOOK_URL`

### Email Digest (gửi daily digest, không phải real-time)

```bash
EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=abcd-efgh-ijkl-mnop
EMAIL_TO=security-team@company.com
```

**Setup Gmail:**

1. Bật 2FA cho Gmail
2. Tạo **App Password**: https://myaccount.google.com/apppasswords
3. Dùng App Password (không dùng password thường)
4. Email digest sẽ được gửi **hàng ngày lúc 08:00 UTC (15:00 VN)** — cấu hình cứng trong `main.py`

**Lưu ý**: Email chỉ gửi digest hàng ngày, không phải real-time như Telegram/Slack.

---

## 6. State Storage

```bash
STATE_DB_PATH=data/varden_state.db
```

Đường dẫn DB riêng của Varden — lưu cursor (last processed article ID), cache analyses, track alerts đã gửi. Không cần chỉnh.

---

## Checklist trước khi `docker compose up -d`

- [ ] Đã copy `.env.example` thành `.env`
- [ ] Đã điền `ANTHROPIC_API_KEY` hoặc `DEEPSEEK_API_KEY`
- [ ] Đã bật ít nhất 1 notification channel (Telegram hoặc Slack)
- [ ] Đã sửa `config/company_profile.yaml` với tech stack thực tế
- [ ] Đã config `ALERT_THRESHOLD` và `ALERT_SEVERITIES` theo nhu cầu
- [ ] Đã verify shared volume giữa Oksskolten và Varden trong `docker-compose.yaml`

## Test trước khi chạy daemon

```bash
# Chạy 1 cycle để kiểm tra pipeline hoạt động
docker compose run --rm varden run

# Xem state hiện tại
docker compose run --rm varden status

# Nếu OK, chạy daemon mode production
docker compose up -d varden
```

## Troubleshooting

**Không nhận được alert nào?**

1. Check `docker compose logs varden` — xem có error không
2. `docker compose run --rm varden status` — xem có analysis nào không
3. Nếu có analysis nhưng không alert: có thể threshold quá cao hoặc severity mismatch
4. Thử hạ `ALERT_THRESHOLD=5` và `ALERT_SEVERITIES=critical,high,medium,low,info` để test gửi — nếu nhận được nghĩa là AI phân loại đúng, chỉ cần tune lại threshold

**Quá nhiều alert?**

1. Tăng `ALERT_THRESHOLD` lên 8 hoặc 9
2. Thu hẹp `ALERT_SEVERITIES` chỉ còn `critical`
3. Kiểm tra `config/company_profile.yaml` — `tech_stack` có quá rộng không
4. Thêm keywords vào `reduce_keywords` của company profile

**Alert gửi đúng channel nhưng format xấu?**

Telegram dùng HTML parse mode — nếu title chứa ký tự `<`, `>`, `&` chưa escape. Đã handle trong code nhưng nếu vẫn lỗi, check `src/notifier.py` → `_escape_html()`.

**AI provider báo rate limit?**

Tăng `POLL_INTERVAL_MINUTES` lên 30, giảm `BATCH_SIZE` xuống 10. Hoặc chuyển sang DeepSeek (rate limit rộng hơn OpenAI/Anthropic free tier).