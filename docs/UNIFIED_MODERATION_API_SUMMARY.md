# Unified Moderation API — Implementation Summary

This document provides a concise, easy-to-read summary of the work done to combine the **Text Moderation API** (DeBERTa model) and the **Image Moderation API** (CLIP visual check & OCR) into a single, unified REST API wrapper.

**Document Path:** `c:\Users\princ\AI_Content_Moderation_System\docs\UNIFIED_MODERATION_API_SUMMARY.md`

---

## 1. Overview of Work Done

We implemented a single wrapper REST endpoint:
`POST /v1/moderate/unified`

It accepts both text and media file uploads (`multipart/form-data` or `application/json`), intelligently detects which inputs are present, and invokes only the appropriate underlying moderation services:

1. **Case 1: Only text is provided**
   - Calls **only** the existing text moderation API (DeBERTa model pipeline).
   - Does **not** call the image moderation API or CLIP visual model.
   - `checks.text.executed = true`, `checks.image.executed = false`.
   - Returns the text moderation result.

2. **Case 2: Only an image/media file is provided**
   - Calls **only** the existing image moderation API (CLIP visual check & OCR).
   - Does **not** call DeBERTa or text moderation on user text.
   - `checks.image.executed = true`, `checks.text.executed = false`.
   - Returns the image moderation result.

3. **Case 3: Both text and image are provided**
   - Calls **both** moderation services.
   - Combines individual results into one unified response.
   - If either check rejects the content (or marks it unsafe), the overall decision is `reject` (never `allow`).
   - `checks.text.executed = true`, `checks.image.executed = true`.
   - Returns individual results in `checks.text.result` and `checks.image.result` alongside the final combined decision.

4. **Validation & Security**
   - At least one of text or media is required; if neither is provided, returns HTTP `400 invalid_request`.
   - Explicitly empty/whitespace text is rejected with HTTP `400 invalid_content`.
   - Invalid file formats return HTTP `422 media_unsupported`.
   - Oversized text or files return HTTP `413 content_too_long` / `media_too_large`.
   - Authentication via Bearer token (`SERVICE_TOKEN`) and client IP blocking are seamlessly enforced.
   - Both original endpoints (`POST /v1/moderate` and `POST /v1/moderate/media`) continue to work untouched.

---

## 2. Files Modified and Created

| Action | File Path | Description |
|---|---|---|
| **Modified** | `c:\Users\princ\AI_Content_Moderation_System\flask_api\app\api.py` | Added `moderate_unified()` endpoint, refactored shared execution helpers (`_execute_moderation`, `_execute_media_moderation`, `_build_visual_reject_result`), and registered `moderate_unified` in `SUBMISSION_ENDPOINTS`. |
| **Created** | `c:\Users\princ\AI_Content_Moderation_System\flask_api\tests\test_unified.py` | Added 20 automated unit/integration tests verifying all 12 required scenarios, mocks, and edge cases. |
| **Modified** | `c:\Users\princ\AI_Content_Moderation_System\docs\openapi.yaml` | Added OpenAPI 3.0 specification for `/v1/moderate/unified` and `UnifiedModerationResultApiResponse`. |
| **Modified** | `c:\Users\princ\AI_Content_Moderation_System\docs\API_REFERENCE.md` | Documented the new endpoint in the official API reference. |
| **Created** | `c:\Users\princ\AI_Content_Moderation_System\docs\UNIFIED_MODERATION_API_SUMMARY.md` | This summary documentation. |

---

## 3. API Request & Response Formats

### Endpoint
`POST /v1/moderate/unified`

### Request Parameters
Supports `multipart/form-data` (recommended for files + text) and `application/json` (for text only).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `content` / `text` | string | Optional* | Text content to moderate (*at least one of text or file required). |
| `file` / `image` | binary | Optional* | Image, PDF, or video attachment (*at least one of text or file required). |
| `content_type` | string | Optional | Surface type (`article` [default], `product_listing`, `post`, etc.). |
| `content_id` | string / int | Optional | Platform content identifier. |

### Response Format
Responses adhere to the official BONC Network `ApiResponse<T>` envelope (`Success`, `Message`, `Data`).

```json
{
  "Success": true,
  "Message": "Content approved for publication.",
  "Data": {
    "decision": "approved",
    "Action": "approved",
    "legacy_decision": "allow",
    "content_type": "article",
    "content_id": "item-101",
    "checks": {
      "text": {
        "executed": true,
        "decision": "approved",
        "result": { "...text moderation details..." }
      },
      "image": {
        "executed": true,
        "decision": "approved",
        "result": { "...image moderation details..." }
      }
    }
  }
}
```

---

## 4. Example PowerShell & Postman Requests

### Case 1: Only Text Provided

#### PowerShell:
```powershell
# Text only via JSON
$headers = @{ "Content-Type" = "application/json" }
$body = @{
    content = "Premium basmati rice exporter, certified organic."
    content_type = "product_listing"
    content_id = "LST-101"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/v1/moderate/unified" -Method Post -Headers $headers -Body $body
```

#### Postman:
- **Method:** `POST`
- **URL:** `http://localhost:8000/v1/moderate/unified`
- **Body:** `raw` (JSON)
```json
{
  "content": "Premium basmati rice exporter, certified organic.",
  "content_type": "product_listing",
  "content_id": "LST-101"
}
```

---

### Case 2: Only Image Provided

#### PowerShell:
```powershell
# Image only via multipart/form-data
$filePath = "C:\path\to\image.png"
$form = @{
    file = Get-Item -Path $filePath
    content_type = "article"
    content_id = "IMG-202"
}
Invoke-RestMethod -Uri "http://localhost:8000/v1/moderate/unified" -Method Post -Form $form
```

#### Postman:
- **Method:** `POST`
- **URL:** `http://localhost:8000/v1/moderate/unified`
- **Body:** `form-data`
  - `file`: (Select File: `image.png`)
  - `content_type`: `article`
  - `content_id`: `IMG-202`

---

### Case 3: Both Text and Image Provided

#### PowerShell:
```powershell
# Both text and image via multipart/form-data
$filePath = "C:\path\to\image.png"
$form = @{
    content = "High quality industrial copper tubes and pipes."
    file = Get-Item -Path $filePath
    content_type = "article"
    content_id = "ART-303"
}
Invoke-RestMethod -Uri "http://localhost:8000/v1/moderate/unified" -Method Post -Form $form
```

#### Postman:
- **Method:** `POST`
- **URL:** `http://localhost:8000/v1/moderate/unified`
- **Body:** `form-data`
  - `content`: `High quality industrial copper tubes and pipes.`
  - `file`: (Select File: `image.png`)
  - `content_type`: `article`
  - `content_id`: `ART-303`

---

## 5. Verification Results

Automated test run command:
```bash
python -m pytest flask_api/tests/test_unified.py flask_api/tests/test_api.py flask_api/tests/test_media.py flask_api/tests/test_service_token.py flask_api/tests/test_client_address.py
```

Result:
- **112 passed, 5 skipped, 0 failures**
- All 12 required scenarios verified and passing.

