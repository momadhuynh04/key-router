# AdCraft Image (`adcraft-image`)

AdCraft gọi một alias duy nhất; key-router chọn backend/model thật.

```
POST http://127.0.0.1:8082/v1/images/generations     (alias: POST /v1/images)
Authorization: Bearer key-router        # proxy không auth, giá trị nào cũng được
{
  "model": "adcraft-image",
  "prompt": "...",
  "n": 1,
  "aspect_ratio": "1:1",                # optional
  "output_format": "png",               # optional: png | jpeg | webp
  "input_references": [                 # optional, tối đa 4
    {
      "type": "input_image",
      "role": "character_reference",    # character | product | scene
      "source": { "type": "image_url", "value": "https://..." }
    }
  ]
}
```

Response đúng 1 ảnh, mỗi item chỉ có 2 key:

```json
{ "created": 1748372400, "data": [{ "b64_json": "...", "media_type": "image/png" }] }
```

Lỗi: status non-2xx + `{"error": {"code": "...", "message": "..."}}`.
Field lạ (`quality`, `size`, `background`, …) bị bỏ qua im lặng.

## Cấu hình

`config.json`:

```json
{
  "model_mappings": {
    "adcraft-image": "openrouter/google/gemini-2.5-flash-image"
  },
  "image_models": {
    "replicate/black-forest-labs/flux-kontext-pro": {
      "backend": "replicate",
      "upstream_model": "black-forest-labs/flux-kontext-pro",
      "max_references": 1,
      "reference_field": "input_image",
      "aspect_ratio_mode": "ratio",
      "supported_aspect_ratios": ["1:1", "16:9", "9:16", "4:3", "3:4"],
      "output_format_field": "output_format"
    }
  }
}
```

- `adcraft-image` khớp **exact**, không dính family fallback (`gpt/codex/o1-4`).
- `image_models` là per-model override. Key là `<backend>/<upstream_model>`.
- Với `openrouter`, capability live từ `GET /api/v1/images/models` được ưu tiên
  cho `max_references`, `supported_aspect_ratios` và việc có gửi `output_format`
  hay không. Config chỉ là fallback khi fetch lỗi.
- Với `replicate`, cấu hình trong `image_models` là nguồn sự thật.
- `aspect_ratio_mode`:
  - `ratio`: gửi `aspect_ratio_field` (mặc định `aspect_ratio`).
  - `size`: map ratio → width×height (`width_field`/`height_field`).
  - `none`: bỏ qua.
- `reference_field`: tên field ảnh tham chiếu của model Replicate
  (`image`, `input_image`, `image_input`, …). Nếu `max_references > 1`, gửi list;
  ngược lại gửi 1 giá trị.

## Xử lý reference

- `max_references = 0` mà request có reference → `422 references_unsupported`
  (không âm thầm bỏ).
- Nhiều hơn giới hạn → cắt theo ưu tiên `character` > `product` > `scene` và log cảnh báo.
- Role được thêm vào cuối prompt, ví dụ:
  `Reference 1 is the character reference.` — giúp model chỉ nhận URL vẫn hiểu vai trò.
- OpenRouter nhận `input_references` dạng **array of objects**, không phải string:
  ```json
  "input_references": [
    { "type": "image_url", "image_url": { "url": "https://… | data:image/png;base64,…" } }
  ]
  ```
  key-router tự convert từ format AdCraft sang shape này.
- Data URL: **gửi thẳng** cho cả OpenRouter và Replicate. Replicate chấp nhận
  `data:` URI cho file input, nên không cần upload tạm. Không log nội dung base64.

## Timeout / retry

- Tổng deadline mặc định 110s (`image_deadline_seconds`), thấp hơn 120s AdCraft chờ.
  Request nhiều reference (3–4 ảnh) có thể vượt 95s; tăng qua env `IMAGE_DEADLINE_SECONDS` nếu cần,
  nhưng phải giữ dưới timeout 120s của AdCraft.
- Replicate: tạo prediction với `Prefer: wait=60`, sau đó poll `urls.get` tới deadline.
- Retry tối đa 1 lần cho 5xx/timeout; không retry 4xx.
- `429` từ upstream được trả nguyên status + `Retry-After` (nếu có).

## API quản lý

| Method | Path | Mô tả |
| --- | --- | --- |
| GET | `/api/adcraft-image/status` | mapping hiện tại, backend/model, endpoint, trạng thái key |
| POST | `/api/adcraft-image/model` | `{ "target": "backend/upstream_model" }` |
| GET | `/api/adcraft-image/models` | danh sách OpenRouter (live) + Replicate (config) |
| POST | `/api/adcraft-image/test` | `{ "with_reference": false }` → sinh ảnh test + preview |

## ENV

Thêm một trong hai (hoặc cả hai) vào `.env`:

```
OPENROUTER_API_KEY="..."
REPLICATE_API_TOKEN="..."
```

Không commit base64 ảnh hay API key; WebUI chỉ hiển thị placeholder `key-router`.

## WebUI

Tab **AdCraft image**: chọn backend (OpenRouter/Replicate), chọn model, Save,
xem endpoint/API key, và hai nút test (không reference / có reference) kèm preview,
thời gian, model thật và kích thước.
