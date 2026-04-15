# Tasks

Thư mục này lưu plan, notes, và tracking cho các task dài hơi của Varden. Áp dụng quy tắc **Plan First** từ global CLAUDE.md.

## Cấu trúc

- `dedup-semantic-plan.md` — Plan semantic deduplication cho alerts (2026-04-15)
- `lessons.md` — Bài học rút ra từ mỗi correction của user (project-specific)

## Quy trình task

1. Mỗi task lớn (≥3 bước hoặc có decision quan trọng) → file plan riêng
2. File plan gồm: context, research findings, decision criteria, phased implementation, risks, decision log
3. Update decision log khi có milestone
4. Khi task done → move vào `tasks/archive/` hoặc để lại với status `✅ Done`
5. Khi có correction từ user → update `lessons.md`
