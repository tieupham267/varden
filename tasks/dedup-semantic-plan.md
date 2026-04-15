# Semantic Deduplication for Varden Alerts

**Created**: 2026-04-15
**Status**: 🚧 Phase 0 + Phase 1 done, waiting for shadow data
**Owner**: tieupham267
**Related issue**: cùng 1 incident được 5-7 feed đưa tin → 5-7 AI call + 5-7 alert → noise

---

## 1. Bối cảnh & Research findings

### Vấn đề
Oksskolten dedup ở URL level (L0 + L1), nhưng bài báo khác nhau về cùng 1 sự kiện (vd "Lazarus đánh crypto exchange" trên BleepingComputer, TheHackerNews, SecurityWeek) → Varden nhận hết, AI-analyze hết, alert hết.

**Impact**:
- Cost: token AI tốn thêm cho duplicate analysis
- Noise: user nhận 5-7 alert cùng nội dung → alert fatigue
- Signal loss: không rõ incident được bao nhiêu nguồn confirm

### Findings từ đọc code Oksskolten (commit hash cần note khi implement)
**Oksskolten có gì:**
- ✅ L0 Exact URL dedup: `articles.url UNIQUE` (migration 0001)
- ✅ L1 URL canonicalization: `server/fetcher/url-cleaner.ts` — strip 60+ tracking params
- ❌ L2 Content hash per article: **KHÔNG có**. "Content hash" trong `rss.ts` là hash feed XML, chỉ để skip parsing khi feed không đổi
- ⚠️ L3 Title similarity: **CÓ** nhưng không dedup
  - `server/similarity.ts`: bigram Dice coefficient trên title
  - Threshold 0.4, window ±3 ngày, skip same-feed
  - Dùng MeiliSearch làm candidate retrieval
  - **Kết quả lưu vào bảng `article_similarities` dạng relationship**
  - Side effect duy nhất: auto-mark-as-read nếu bài giống đã được đọc
  - Bài vẫn tồn tại cả 2 trong DB

**Bảng `article_similarities` schema** (migration 0004):
```sql
CREATE TABLE article_similarities (
  article_id    INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  similar_to_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  score         REAL NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (article_id, similar_to_id)
);
```

### 3 options implementation
| Option | Kỹ thuật | Ưu | Nhược |
|--------|---------|-----|-------|
| **A** | Query `article_similarities` có sẵn | Zero embedding cost, tận dụng Oksskolten | Dice title yếu hơn embedding, có thể miss paraphrase mạnh |
| **B** | Embedding riêng (sentence-transformers) | Chính xác nhất, bắt được paraphrase | Thêm dependency 80MB, duplicate effort |
| **C** | Hybrid A + B | Cân bằng precision/recall | Phức tạp hơn |

**Khuyến nghị**: bắt đầu Option A, đo lường, upgrade sau nếu cần.

---

## 2. Decision criteria — Khi nào upgrade từ A → C?

Sau 2 tuần shadow mode, chốt quyết định dựa vào matrix:

| Agreement rate | Precision audit | Alert reduction | Action |
|----------------|-----------------|-----------------|--------|
| ≥ 0.90 | ≥ 0.95 | > 15% | **Bật Option A** alert-level |
| ≥ 0.90 | ≥ 0.95 | < 10% | Không bật. Noise không phải vấn đề |
| 0.75-0.90 | 0.85-0.95 | > 15% | **Upgrade Option C** |
| < 0.75 | < 0.85 | > 15% | **Option B** (full embedding) |
| bất kỳ | < 0.80 | bất kỳ | **Dừng** — điều tra false case |

### 3 metrics

**M1. Agreement rate** (tự động)
- Trong shadow log, với các cặp `would_merge = true`:
- Đếm cặp "đồng thuận" = CVE overlap ≥ 50% AND |relevance_A - relevance_B| ≤ 2 AND severity_A == severity_B
- `agreement_rate = đồng_thuận / would_merge`

**M2. Alert reduction** (tự động)
- `reduction = 1 - (distinct_clusters_with_alert / total_alerts_sent)`

**M3. Precision audit** (manual, 5-10 phút/tuần)
- CLI `python main.py audit-dedup --sample 20` show 20 cặp random
- User label: `correct` / `false_merge`
- `precision = correct / total_labeled`

---

## 3. Phased implementation plan

### Phase 0: Foundation — Shadow log infrastructure ✅ Done 2026-04-15

Mục tiêu: log decision mà không thay đổi behavior thật.

- [x] Thêm migration `src/state.py`: bảng `dedup_shadow_log`
  - Columns: article_id, matched_article_id, dice_score, would_merge, matched_already_analyzed, relevance_score, severity, cves_json, matched_relevance_score, matched_severity, matched_cves_json, audit_label, audit_note, created_at
  - 3 indexes: created_at, would_merge, audit_label
- [x] Thêm module `src/dedup.py`:
  - `query_oksskolten_similarities(article_id, min_score=0.6)` → bidirectional lookup trên `article_similarities` table
  - `record_shadow_decision(article, analysis)` → compute + log (exception-safe)
  - `_extract_cves()` helper chuẩn hóa CVE field
- [x] `src/state.py`: helper `get_analysis()`, `log_shadow_decision()`, `get_shadow_stats()`
- [x] Hook vào `src/pipeline.py`: sau `save_analysis` → `record_shadow_decision`
- [x] Unit tests: 14 tests cover query (empty DB, missing table, threshold filter, bidirectional lookup, score sort) + shadow logging (no match, match without prior, would_merge with prior, silent error)
- [x] Full test suite pass: 137/137
- [ ] Deploy, xác nhận log populate sau 1 cycle (pending deploy)

**Done criteria**: sau 1 ngày chạy, query `SELECT COUNT(*) FROM dedup_shadow_log` > 0, không regression nào.

**Files changed**:

- `src/state.py` — thêm table `dedup_shadow_log` + 3 helpers
- `src/dedup.py` — module mới (167 dòng)
- `src/pipeline.py` — 2 dòng hook
- `tests/conftest.py` — extend fixture `oksskolten_db` với `article_similarities` table
- `tests/test_dedup.py` — mới (14 tests)

**Config mới** (optional):

- `DEDUP_MIN_SCORE` env var (default 0.6) — threshold Dice cao hơn Oksskolten để giảm false positive ở shadow log

### Phase 1: Metrics automation ✅ Done 2026-04-15

Mục tiêu: tự động compute M1, M2, M3.

- [x] Module `src/metrics.py`:
  - `compute_metrics(days)` returns `DedupMetrics` dataclass
  - `_cve_overlap()`, `_is_agreement()` helpers
  - `format_report()` human-readable output
  - `_recommend()` map metrics → decision matrix
- [x] CLI `python main.py dedup-metrics [--days=14] [--json]`
- [x] Unit tests: 25 tests cover CVE overlap, agreement logic (6 edge cases), compute_metrics (empty/coverage/agreement/alert reduction/audit), recommend logic (6 scenarios), format report
- [x] Full test suite: 162/162 pass
- [x] CLI smoke test: both text and JSON output verified

**Deferred** (không cần lúc này):

- Scheduled weekly Telegram report - user có thể cron `docker exec varden python main.py dedup-metrics` hoặc add sau khi có data

**Files changed**:

- `src/metrics.py` - mới (240 dòng)
- `main.py` - thêm `cmd_dedup_metrics`
- `tests/test_metrics.py` - mới (25 tests)

**Done criteria đạt**: chạy command ra report có ý nghĩa, cả text lẫn JSON mode.

### Phase 2: Manual audit CLI (1 giờ)
Mục tiêu: support M3 measurement.

- [ ] CLI `python main.py audit-dedup --sample 20`:
  - SELECT 20 cặp WHERE audit_label IS NULL, ORDER BY RANDOM()
  - Show: title_A, title_B, dice_score, severity_A vs B, CVE overlap
  - Prompt: [c]orrect / [f]alse_merge / [s]kip / [q]uit
  - Write audit_label + audit_note

**Done criteria**: label được 20+ cặp, query `SELECT audit_label, COUNT(*) FROM dedup_shadow_log GROUP BY 1` ra số có nghĩa.

### Phase 3: Decision point (sau 2 tuần shadow)
- [ ] Chạy `dedup-metrics --days 14` → lấy 3 metrics
- [ ] So sánh với decision matrix
- [ ] Document quyết định vào file này (section 5 bên dưới)

### Phase 4: Production rollout (chỉ làm nếu Phase 3 OK)
Alert-level dedup (vẫn AI-analyze đầy đủ, chỉ gộp alert):

- [ ] Thêm `cluster_id` column vào analysis cache
- [ ] Modify `src/notifier.py`: nếu article thuộc existing cluster có alert → update alert cũ ("+1 source confirmed") thay vì gửi mới
- [ ] Feature flag `ENABLE_SEMANTIC_DEDUP=false` mặc định, bật manual sau verify
- [ ] Rollback plan: set flag=false → revert về behavior cũ ngay

**Done criteria**: 1 tuần production không có false merge complaint, alert reduction đạt mục tiêu.

### Phase 5 (optional): Token savings
Chỉ làm nếu Phase 4 stable **và** token cost thực sự là pain point:

- [ ] Skip AI-analyze nếu bài thuộc cluster đã analyze
- [ ] Re-analyze trigger: nếu cluster có bài thứ 3+ sau 6h
- [ ] Theo dõi false skip rate qua feedback loop

### Phase 6 (optional): Self-tuning feedback
- [ ] Telegram reactions: `👎 duplicate` / `⚠️ false merge`
- [ ] Bảng `dedup_feedback`, weekly auto-tune threshold

---

## 4. Risks & mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| False merge → mất alert về incident khác | HIGH | Phase 4 chỉ dedup alert, không skip analyze. Feature flag rollback nhanh |
| Oksskolten `article_similarities` không được populate (MeiliSearch down) | MEDIUM | Check `isSearchReady()` trước khi query. Graceful fallback về no-dedup |
| Threshold tuning sai → hoặc quá gộp hoặc không gộp gì | MEDIUM | Shadow mode 2 tuần trước khi bật. Decision matrix có tiers |
| Oksskolten schema đổi (similarity table bị xoá/đổi tên) | LOW | Test integration mỗi lần Oksskolten update. Graceful degrade |
| Performance: query similarity mỗi article | LOW | Index trên (article_id, score) đã có. Benchmark trong Phase 0 |

---

## 5. Decision log (fill khi tới timeline)

### 2026-04-15: Plan created
- Đã research Oksskolten code, xác định Option A khả thi
- Quyết định: bắt đầu Phase 0 sau khi user approve plan này
- **Open question**: user có muốn implement ngay hay chờ priority task khác?

### Phase 3 decision (fill sau 2 tuần):
- Date:
- M1 (agreement): _
- M2 (alert reduction): _
- M3 (precision audit, n=_): _
- Decision: [A | B | C | stop]
- Rationale:

---

## 6. References

- Oksskolten repo: https://github.com/babarot/oksskolten
- Key files researched:
  - `server/similarity.ts` — Dice coefficient implementation
  - `server/fetcher/url-cleaner.ts` — L1 canonicalization
  - `server/fetcher/rss.ts` — feed-level content hash (NOT article-level)
  - `migrations/0001_initial.sql` — `articles.url UNIQUE`
  - `migrations/0004_article_similarities.sql` — similarity storage schema
- Related Varden files (sẽ đụng):
  - `src/pipeline.py` — orchestration hook point
  - `src/state.py` — thêm migration shadow_log
  - `src/source.py` — query Oksskolten DB (cần extend để đọc article_similarities)
  - `src/notifier.py` — alert-level dedup logic (Phase 4)
  - `main.py` — thêm CLI commands

---

## 7. Not in scope (ghi lại kẻo quên)

Những thứ phát hiện khi research nhưng KHÔNG làm trong task này:
- Threat actor tracking với profile database
- CVE enrichment từ NVD/CISA KEV
- IOC extraction & STIX export
- Weekly theme-based digest
- Feedback loop UI cho tuning relevance (separate concern)
