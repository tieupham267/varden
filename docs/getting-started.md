# Hướng dẫn cài đặt Varden từ đầu

Hướng dẫn step-by-step từ zero đến chạy production.

---

## Yêu cầu

- **Docker** và **Docker Compose** (khuyến nghị)
- Hoặc **Python 3.12+** nếu chạy trực tiếp
- **API key** của một AI provider: Anthropic (Claude), DeepSeek, hoặc OpenAI-compatible
- **Oksskolten** đã chạy và có dữ liệu RSS (hoặc sẽ cài song song)

---

## Bước 1: Clone repo

```bash
git clone https://github.com/tieupham267/varden varden
cd varden
```

---

## Bước 2: Tạo file `.env`

Copy từ template và mở ra sửa:

```bash
cp .env.example .env
```

**Tối thiểu cần điền:**

```bash
# Chọn 1 AI provider và điền API key
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxx

# Bật ít nhất 1 kênh notification
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
```

> Chi tiết từng biến xem [configuration.md](configuration.md).

### Tạo Telegram bot (nếu chưa có)

1. Chat với [@BotFather](https://t.me/botfather) trên Telegram → gõ `/newbot`
2. Đặt tên bot → nhận `TELEGRAM_BOT_TOKEN`
3. Tạo group → add bot vào → gửi 1 tin nhắn bất kỳ
4. Lấy chat ID:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates"
   ```
   Tìm `"chat":{"id":-1001234567890,...}` → đó là `TELEGRAM_CHAT_ID`

---

## Bước 3: Cấu hình Company Profile

Sửa `config/company_profile.yaml` cho khớp với hạ tầng thực tế của công ty:

```bash
# Mở file
nano config/company_profile.yaml   # hoặc dùng editor bất kỳ
```

**Cần sửa:**

- `company.name` — tên công ty
- `company.sector` — ngành nghề (financial_services, healthcare, technology...)
- `tech_stack` — **quan trọng nhất**: liệt kê chính xác sản phẩm/vendor đang dùng. Càng cụ thể, AI cho điểm càng chính xác.
- `watched_threat_actors` — threat actors liên quan đến sector/geography của bạn
- `boost_keywords` / `reduce_keywords` — từ khóa tăng/giảm điểm relevance

> File mặc định đã có template cho công ty tài chính Việt Nam. Xoá/thêm theo thực tế.

---

## Bước 4: Cài đặt Oksskolten (nếu chưa có)

Varden cần Oksskolten để lấy bài viết. Nếu đã có Oksskolten chạy sẵn, bỏ qua bước này.

### Import RSS feeds

File `security-feeds.opml` đi kèm repo chứa 30+ nguồn RSS bảo mật đã chọn lọc, bao gồm:

- **Tin tức**: Krebs on Security, The Hacker News, BleepingComputer, Dark Reading...
- **CVE/Advisory**: CISA KEV, NIST NVD, Zero Day Initiative
- **Vendor advisories**: Microsoft MSRC, Fortinet, Cisco, VMware, Palo Alto
- **APT research**: Mandiant, CrowdStrike, Google TAG, Kaspersky, Unit 42...
- **Vietnam/APAC**: VNCERT/CC, JPCERT/CC, AusCERT

Import file này vào Oksskolten để có nguồn dữ liệu ngay.

---

## Bước 5: Chạy với Docker (khuyến nghị)

### 5a. Chạy cả Oksskolten + Varden

```bash
docker compose up -d
```

Lệnh này sẽ:
- Khởi động Oksskolten (port 3000)
- Build và khởi động Varden ở daemon mode
- Tạo shared volume `oksskolten_data` giữa 2 container
- Varden đọc DB Oksskolten qua volume này (read-only)

### 5b. Nếu Oksskolten đã chạy riêng

Sửa `docker-compose.yaml` để trỏ volume đến đúng data directory của Oksskolten:

```yaml
volumes:
  oksskolten_data:
    external: true
    name: ten-volume-oksskolten-cua-ban
```

Hoặc dùng bind mount:

```yaml
varden:
  volumes:
    - /duong-dan/den/oksskolten/data:/oksskolten-data:ro
```

### 5c. Test trước khi chạy production

```bash
# Chạy 1 cycle để verify pipeline hoạt động
docker compose run --rm varden run

# Xem kết quả
docker compose run --rm varden status
```

Nếu output hiện danh sách articles với relevance score → pipeline hoạt động đúng.

### 5d. Chạy production

```bash
# Start daemon mode (poll mỗi 15 phút)
docker compose up -d varden

# Monitor logs
docker compose logs -f varden
```

---

## Chạy trực tiếp (không Docker)

Nếu không dùng Docker:

```bash
# Cài dependencies
pip install -r requirements.txt

# Sửa đường dẫn DB trong .env
# OKSSKOLTEN_DB_PATH=/duong-dan/den/oksskolten.db

# Test 1 cycle
python main.py run

# Xem state
python main.py status

# Chạy daemon
python main.py daemon
```

---

## Bước 6: Verify hoạt động

### Kiểm tra logs

```bash
docker compose logs -f varden
```

Output bình thường:

```
Starting pipeline cycle
Cursor: last processed article ID = 0
Fetched 15 new articles
  Analyzing #1: Critical FortiGate vulnerability...
    → relevance=9/10, severity=critical, reason: FortiGate in tech stack
    → ALERTING (score 9 >= 7)
  Analyzing #2: New gaming platform breach...
    → relevance=1/10, severity=medium, reason: Gaming not relevant
Cycle complete: analyzed=15, alerted=2, skipped=0, failed=0
Cursor advanced to 15
```

### Kiểm tra notification

Nếu có bài viết vượt ngưỡng alert, bạn sẽ nhận được tin nhắn Telegram/Slack với format:

```
🔴 [CRITICAL] Relevance: 9/10 ●●●●●●●●●○

Fortinet FortiGate RCE Vulnerability (CVE-2026-XXXXX)

📝 Lỗ hổng RCE nghiêm trọng trên FortiGate cho phép attacker thực thi
   code từ xa không cần xác thực...

🎯 FortiGate nằm trong tech stack của công ty (network_security)

💡 Khuyến nghị:
  • Patch ngay FortiOS lên phiên bản mới nhất
  • Kiểm tra log firewall 30 ngày gần đây
```

### Không nhận được alert?

1. Kiểm tra Oksskolten đã có dữ liệu chưa: `docker compose run --rm varden status`
2. Nếu `Last processed article ID: 0` và `Analyses in last 24h: 0` → Oksskolten chưa fetch bài nào
3. Nếu có analyses nhưng không alert → threshold cao hoặc severity mismatch. Thử hạ `ALERT_THRESHOLD=5` trong `.env`

> Xem thêm troubleshooting trong [configuration.md](configuration.md#troubleshooting).

---

## Tóm tắt nhanh

| Bước | Thao tác | Thời gian |
|------|----------|-----------|
| 1 | Clone repo | 1 phút |
| 2 | Tạo `.env`, điền API key + Telegram | 5 phút |
| 3 | Sửa `company_profile.yaml` | 10-15 phút |
| 4 | Setup Oksskolten + import feeds | 5-10 phút |
| 5 | `docker compose up -d` | 2 phút |
| 6 | Verify logs + nhận alert đầu tiên | Chờ Oksskolten fetch RSS |
