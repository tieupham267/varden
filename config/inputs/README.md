# Company Description Inputs

Đặt các file mô tả công ty vào thư mục này. Lệnh `python main.py profile-gen`
sẽ đọc tất cả file hợp lệ, gửi qua LLM, và sinh ra
`config/company_profile.generated.yaml`.

## Định dạng được hỗ trợ

- `.md` — Markdown (khuyến nghị cho mô tả dạng văn bản)
- `.txt` — Plain text
- `.yaml` / `.yml` — Dữ liệu có cấu trúc (ví dụ asset inventory)
- `.json` — Dữ liệu có cấu trúc
- `.csv` — Asset inventory dạng bảng

## Ví dụ layout

```
config/inputs/
├── 01-company-overview.md      # Tên, ngành, quy mô, địa điểm
├── 02-business-operations.md   # Sản phẩm, dịch vụ, tập khách hàng
├── 03-tech-stack.md            # Hệ điều hành, mạng, bảo mật, DB, cloud
├── 04-asset-inventory.csv      # Hoặc YAML/JSON
└── 05-compliance-context.md    # Khung pháp lý, chuẩn đang tuân thủ
```

Thứ tự file không quan trọng — LLM sẽ đọc hết và tổng hợp.

## Lưu ý

- Tổng dung lượng giới hạn ~100.000 ký tự để tránh prompt quá lớn.
- Output **không tự động** ghi đè `company_profile.yaml`. File sinh ra có hậu tố
  `.generated.yaml` để bạn review và rename tay.
- Các suggest về `watched_threat_actors` và `priority_techniques` do LLM suy luận
  từ sector/geo — **luôn review thủ công** trước khi dùng cho production.
- File `README.md` này được `profile_generator.py` bỏ qua để không lẫn vào prompt.
- Thư mục này đã được `.gitignore` (trừ README) vì file mô tả có thể chứa thông
  tin nhạy cảm.
