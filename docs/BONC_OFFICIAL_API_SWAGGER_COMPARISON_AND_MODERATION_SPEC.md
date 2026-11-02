# BONC Network: Official Swagger API Audit, Cross-System Comparison & Moderation Response Standardization

> **Document Version:** 1.0  
> **Status:** Production Standard  
> **Author:** Antigravity AI Engineering & BONC Architecture Team  
> **Reference Official Swagger:** [Production - BONC Network Microservice V1 (`https://api.boncnetwork.com/swagger/index.html`)](https://api.boncnetwork.com/swagger/index.html)  
> **Target Service:** BONC Moderation Engine (`flask_api` / Python ML Backend)  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Official BONC Network Swagger API Audit (641 Endpoints / 71 Tags)](#2-official-bonc-network-swagger-api-audit-641-endpoints--71-tags)
   - [2.1 Backend Platform & Architecture](#21-backend-platform--architecture)
   - [2.2 Functional Domain Breakdown](#22-functional-domain-breakdown)
   - [2.3 Content Ingestion & User-Facing Surfaces](#23-content-ingestion--user-facing-surfaces)
   - [2.4 Official Enforcement & Moderation Endpoints](#24-official-enforcement--moderation-endpoints)
3. [Deep Comparative Analysis: Official APIs vs Moderation APIs](#3-deep-comparative-analysis-official-apis-vs-moderation-apis)
   - [3.1 Architectural & Paradigm Comparison](#31-architectural--paradigm-comparison)
   - [3.2 Response Envelope Design](#32-response-envelope-design)
   - [3.3 Error Contract & RFC 7807/9110 Alignment](#33-error-contract--rfc-78079110-alignment)
   - [3.4 .NET Verdict Alignment: The "approved" vs "reject" Contract](#34-net-verdict-alignment-the-approved-vs-reject-contract)
   - [3.5 Surface & Entity Cross-Reference Matrix](#35-surface--entity-cross-reference-matrix)
4. [Standardized Moderation API Response Specification](#4-standardized-moderation-api-response-specification)
   - [4.1 Universal Envelope Schema (`ApiResponse<T>`)](#41-universal-envelope-schema-apiresponset)
   - [4.2 Moderation Approved Response (`Action: "approved"`)](#42-moderation-approved-response-action-approved)
   - [4.3 Moderation Rejected Response (`Action: "reject"`)](#43-moderation-rejected-response-action-reject)
   - [4.4 Asynchronous 202 Accepted Response (`Action: "pending"`)](#44-asynchronous-202-accepted-response-action-pending)
   - [4.5 Multi-Modal Media Moderation (`POST /v1/moderate/media`)](#45-multi-modal-media-moderation-post-v1moderatemedia)
   - [4.6 Error Response Contract](#46-error-response-contract)
   - [4.7 Operational Endpoints (`/health`, `/ready`)](#47-operational-endpoints-health-ready)
5. [.NET Microservice Integration Guide & C# Implementation](#5-net-microservice-integration-guide--c-implementation)
   - [5.1 C# DTO Definitions](#51-c-dto-definitions)
   - [5.2 HttpClient Integration Pattern](#52-httpclient-integration-pattern)
   - [5.3 Status Transition Workflow](#53-status-transition-workflow)
6. [Backward Compatibility & Client SDK Verification](#6-backward-compatibility--client-sdk-verification)

---

## 1. Executive Summary

This document establishes the definitive specification connecting the **BONC Network Official Microservice APIs** (`api.boncnetwork.com`) with the **BONC Moderation Engine** (`flask_api`).

Historically, the moderation engine was developed as an autonomous Python microservice returning flat JSON payloads (`{"decision": "allow" | "revise" | "reject", "risk_score": ...}`) on success and dual error envelopes (`{"error": {"code": ...}}` vs `{"error": "..."}`) on failure. In contrast, the company's official backend—built on Microsoft .NET (ASP.NET Core / IIS 10.0)—standardizes on an `ApiResponse<T>` envelope (`Success`, `Message`, `Type`, `Data`), RFC 9110 problem details, and binary content moderation actions: **`"approved"`** and **`"reject"`**.

Following a full inspection of the official Swagger definition (`https://api.boncnetwork.com/swagger/v1/swagger.json`), our moderation APIs have been upgraded to:
1. Encapsulate all outcomes in the company standard **`ApiResponse<T>`** envelope.
2. Directly emit the two canonical actions required by the .NET backend: **`"approved"`** and **`"reject"`**.
3. Retain backwards-compatibility aliases (`decision`, `legacy_decision`, flattened root fields) ensuring zero regression for existing audit sinks, web client scripts (`moderation-client.js`), and unit test suites.

---

## 2. Official BONC Network Swagger API Audit (641 Endpoints / 71 Tags)

### 2.1 Backend Platform & Architecture

Inspection of live HTTP responses and the OpenAPI 3.0.1 specification (`bonc_swagger.json`) reveals:
* **Host Gateway:** `https://api.boncnetwork.com`
* **Server Framework:** Microsoft-IIS/10.0 with ASP.NET Core Web API
* **Total Endpoints:** 641 distinct HTTP operations
* **Total Controllers / Tags:** 71 functional groups
* **Total Components / DTO Schemas:** 390 schema models
* **Authentication Scheme:** Bearer JWT tokens (`Authorization: Bearer <token>`)

### 2.2 Functional Domain Breakdown

| Functional Domain | Key Swagger Tags | Endpoint Count | Purpose |
|---|---|---|---|
| **Content Publishing & Social** | `Article`, `NetworkArticle`, `Post`, `GroupPost`, `ShortVideo`, `MarketingVideo`, `Comments`, `Publish`, `MetaTags` | 138 | Core user content generation: articles, whitepapers, social posts, group discussions, short-form videos, and interactive comment threads. |
| **B2B Marketplace & Commercial** | `ProductsAndServicesApi`, `B2BProposal`, `BusinessProposal`, `Job`, `InquiriesApi`, `OrdersApi`, `DiscountCouponsApi`, `LoanApi` | 134 | Commercial transactions: product catalog listings, RFPs, formal enterprise proposals, job openings, and financing inquiries. |
| **Profiles & Corporate Identity** | `UserProfile`, `CompanyProfile`, `CompanyApi`, `CorporateWhitelist`, `RolesApi`, `IdValidation` | 69 | Member verification, company directories, verified corporate domain whitelisting, and compliance document KYC. |
| **Real-Time Communication** | `Conversations`, `Messages`, `ChatContacts`, `ChatInvites` | 48 | Direct peer-to-peer and group messaging, automated contact matching, and secure invite distribution. |
| **Moderation & Enforcement** | `Moderation`, `AdminArticle`, `Article` (actions), `Job` (actions) | 9 | User reporting, user blocking, and administrative action endpoints for articles and job postings. |
| **Media, Search & System** | `Media`, `Banner`, `ImageMigration`, `Search`, `CommonApi` | 78 | Object uploads, CDN asset management, full-text Elasticsearch/Azure Search indexing, and platform-wide utility APIs. |

### 2.3 Content Ingestion & User-Facing Surfaces

Content enters the BONC platform through specific user-facing endpoints that require automated moderation checks before publication:

1. **Articles & Whitepapers:**
   * `POST /api/Article/CreateArticle` & `PUT /api/Article/UpdateArticle/{articleId}`
   * `POST /api/NetworkArticle/CreateArticle`
   * Fields: `Title`, `Body` (HTML/rich-text), `ThumbnailUrl`, `Hashtags`, `Medias` (PDF/images).
2. **Social Posts & Queries:**
   * `POST /api/Post/createPost` & `POST /api/Post/addQuickQueryPost`
   * `POST /api/GroupPost/CreateGroupPost`
   * Fields: `Content`, `MediaUrls`, `Tags`, `MentionedUsers`.
3. **Short & Marketing Videos:**
   * `POST /api/ShortVideo/addComment`, `POST /api/MarketingVideo/...`
   * Fields: `Title`, `Description`, `VideoUrl`, `Tags`.
4. **B2B Proposals & RFPs:**
   * `POST /api/B2BProposal`, `POST /api/BusinessProposal`
   * Fields: `Title`, `Requirement`, `Budget`, `TermsAndConditions`.
5. **Product & Service Catalog:**
   * `POST /api/ProductsAndServicesApi`
   * Fields: `Name`, `Description`, `Specifications`, `Price`, `Category`.
6. **User & Company Profiles:**
   * `POST /api/UserProfile`, `PUT /api/CompanyProfile`
   * Fields: `Bio`, `AboutUs`, `Certifications`, `Tagline`.

### 2.4 Official Enforcement & Moderation Endpoints

The official Swagger explicitly defines several moderation and enforcement contracts:

* **User Reporting (`POST /api/users/{id}/report`):**
  * Request Body: `ReportRequest` (`Reason`: int32 enum `[0, 1, 2, 3, 4, 99]`, `Details`: string, `MessageId`: UUID).
  * Response: Standard `ApiResponse` (`Success: true, Message: string, Data: null`).
* **User Blocking (`POST /api/users/{id}/block` & `DELETE /api/users/{id}/block`):**
  * Path parameter `id` (UUID). Returns standard `ApiResponse`.
* **Admin Article Action (`PUT /api/AdminArticle/action/{source}/{articleId}`):**
  * Request Body: `AdminArticleActionDto` (`Action`: string, `Note`: string).
  * Direct alignment: The two operational values for `Action` are `"approved"` and `"reject"`.
* **Standard Article Action (`POST /api/Article/articleAction`):**
  * Request Body: `ArticleAdminActionDto` (`ArticleId`: UUID, `ArticleSource`: string, `Action`: string, `Note`: string).
* **Job Action (`PUT /api/Job/admin/jobAction`):**
  * Request Body: `AdminJobActionDTO` (`JobId`: UUID, `Status`: string, `Note`: string).

---

## 3. Deep Comparative Analysis: Official APIs vs Moderation APIs

### 3.1 Architectural & Paradigm Comparison

| Dimension | Official BONC Network APIs (`api.boncnetwork.com`) | BONC Moderation Engine (`flask_api`) | Standardized Alignment |
|---|---|---|---|
| **Tech Stack** | Microsoft .NET 8 / C# (ASP.NET Core) | Python 3.12 / Flask 3.1 / WSGI | Decoupled microservice architecture over standard HTTP/REST. |
| **Primary Ingestion** | Entity-specific DTOs (`CreateArticleDto`, `CreatePostDto`) | Consolidated text (`content`) + surface tag (`content_type`) | Moderation client concatenates fields into one text payload with span mapping. |
| **Response Wrapping** | Mandatory `ApiResponse<T>` envelope | Previously raw, un-enveloped JSON dictionary | **Enveloped with `Success`, `Message`, `Type`, `Data`.** |
| **Verdict Terminology** | **`"approved"`** and **`"reject"`** | Previously `"allow"`, `"revise"`, `"reject"` | **Moderation directly returns `"Action": "approved"` and `"Action": "reject"`.** |
| **Error Format** | RFC 9110 `ValidationProblemDetails` / `ApiResponse` | Dual custom shapes (`nested` vs `flat`) | **Unified `ApiResponse` error envelope with nested `Error` details.** |
| **Property Casing** | PascalCase (`Success`, `Message`, `Data`, `Action`) | snake_case (`schema_version`, `risk_score`) | **Envelope & .NET fields in PascalCase; ML metrics in snake_case inside `Data`.** |

### 3.2 Response Envelope Design

In the official company backend, every API endpoint yields an envelope defined by `ApiResponse`:

```json
{
  "Success": true,
  "Message": "Articles fetched successfully.",
  "Type": "success",
  "Data": { ... }
}
```

On operational or entity failure:
```json
{
  "Success": false,
  "Message": "Article not found.",
  "Type": "error",
  "Data": null
}
```

Our moderation engine now wraps all successful evaluations, media checks, and admin controls within this identical structure.

### 3.3 Error Contract & RFC 7807/9110 Alignment

Previously, `docs/API_REFERENCE.md` documented an architectural inconsistency:
* `_error()` in `app/api.py` generated `{ "error": { "code": "...", "message": "..." } }`.
* `_err()` in admin modules generated `{ "error": "code", "message": "..." }`.

Both shapes have been superseded by the unified company error contract:

```json
{
  "Success": false,
  "Message": "body must be a JSON object",
  "Type": "error",
  "Data": null,
  "Error": {
    "code": "invalid_json",
    "message": "body must be a JSON object"
  },
  "error": {
    "code": "invalid_json",
    "message": "body must be a JSON object"
  }
}
```

This guarantees:
1. **.NET HttpClients** deserialize cleanly via `Success == false` and read `Message`.
2. **RFC 7807 / ProblemDetails consumers** obtain machine-readable codes in `Error.code`.
3. **Legacy clients & existing unit tests** continue reading `body["error"]["code"]` without breakage.

### 3.4 .NET Verdict Alignment: The "approved" vs "reject" Contract

The BONC .NET microservice architecture requires content to resolve to one of two definitive states for publishing workflows:
1. **`"approved"`:** The content passed all safety thresholds. The .NET server immediately transitions the item status to `Published` / `Active`.
2. **`"reject"`:** The content violated safety policy, matched a Layer 1 gate block, or failed visual check. The .NET server retains the item as `Draft` or flags it for review, saving the moderation reason into the entity's `Note` column.

The moderation engine maps internal pipeline stages directly into this binary decision:
* Gate allow + Model score $\le$ threshold $\longrightarrow$ **`Action: "approved"`**, **`decision: "approved"`**
* Gate block, visual violation, or Model score $>$ threshold $\longrightarrow$ **`Action: "reject"`**, **`decision: "reject"`**
* Borderline or revise rule hits $\longrightarrow$ **`Action: "reject"`**, but **`decision: "revise"`** (accompanied by user-corrective instructions in `Note` and `feedback`)

`Action` is binary, as the .NET workflow requires. `decision` is not: a revise band keeps the
word `"revise"` there, because the author-facing surfaces have to tell "fix the highlighted
part and resubmit" apart from a flat refusal, and `Action` alone cannot carry that. A .NET
caller should read `Action`; one that reads `decision` must treat `"revise"` as a rejection.
`legacy_decision` always carries the pipeline's own word (`allow` / `revise` / `reject`).

### 3.5 Surface & Entity Cross-Reference Matrix

| Official Swagger Entity / Endpoint | Moderation `content_type` | Evaluated Content Elements | Moderation Pipeline Stages |
|---|---|---|---|
| `POST /api/Article/CreateArticle` | `article` | Title, Body, Link URLs, Hashtags | Gate rules, DeBERTa v3 text model, Sentence Scan, Word Trigger attribution, Public Grievance model. |
| `POST /api/NetworkArticle/CreateArticle` | `network_article` | Title, Body, Group context, Links | Gate rules, DeBERTa v3, Public Grievance model, Sentence Scan. |
| `POST /api/Post/createPost` | `post` | Post text, User tags, Links | Gate rules, DeBERTa v3, Sentence Scan, Window Scan. |
| `POST /api/GroupPost/CreateGroupPost` | `group_post` | Post text, Group ID, Attachments | Gate rules, DeBERTa v3, Sentence Scan. |
| `POST /api/ProductsAndServicesApi` | `product_listing` | Product name, Description, Specs | Gate rules (scam/counterfeit filters), DeBERTa v3 model. |
| `POST /api/B2BProposal`, `BusinessProposal` | `proposal`, `business_proposal` | Title, Scope of work, Payment terms | Gate rules, DeBERTa v3 (Grievance model disabled on private proposals). |
| `POST /api/Job` (Job Postings) | `request` / `advertisement` | Role title, Description, Compensation | Gate rules (job scam / advance fee detection), DeBERTa v3. |
| `POST /api/ShortVideo`, `MarketingVideo` | `video` | Title, Description, Hashtags | Text moderation on metadata; footage pass-through (`checked: false`). |
| `POST /api/UserProfile`, `CompanyProfile` | `business_profile` | Bio, Tagline, About Us, Address | Gate rules (impersonation, contact leakage), DeBERTa v3. |
| Any Image Upload (`Article`, `Post`, `KYC`) | `image` (via `/v1/moderate/media`) | Multi-pass: OCR text extraction + CLIP visual policy | RapidOCR for words (Devanagari/English); CLIP ViT-B/32 against visual policy labels. |

---

## 4. Standardized Moderation API Response Specification

### 4.1 Universal Envelope Schema (`ApiResponse<T>`)

Every moderation endpoint returns responses encapsulated in the standard envelope:

```typescript
interface ApiResponse<T> {
  Success: boolean;       // true for 200/202, false for 4xx/5xx
  Message: string;        // Human-readable summary (e.g. "Content approved for publication.")
  Type: "success" | "warning" | "error" | "info";
  Data: T | null;         // Detailed moderation result payload
}
```

### 4.2 Moderation Approved Response (`Action: "approved"`)

**HTTP Status:** `200 OK`  
**Endpoint:** `POST /v1/moderate`

```json
{
  "Success": true,
  "Message": "Content approved for publication.",
  "Type": "success",
  "Data": {
    "Action": "approved",
    "Decision": "approved",
    "action": "approved",
    "decision": "approved",
    "verdict": "approved",
    "approval_status": "approved",
    "legacy_decision": "allow",
    "Note": null,
    "status": "completed",
    "content_id": "LST-10293",
    "content_type": "product_listing",
    "content": "Premium basmati rice exporter, FSSAI certified.",
    "content_sha256": "9f2c3b8a1e...",
    "risk_score": 0.041,
    "predicted_label": "safe",
    "label_scores": {
      "safe": 0.959,
      "spam": 0.030,
      "fraud": 0.011
    },
    "gate_matches": [],
    "targeted_segments": [],
    "sentence_scores": [],
    "word_scores": [],
    "feedback": null,
    "gate_version": 2,
    "model_version": "deberta-v3-small-bonc-v9-ver3",
    "thresholds": {
      "allow_max": 0.5,
      "reject_min": 0.5
    },
    "moderation": {
      "content_type": "product_listing",
      "enabled": true,
      "level": "medium",
      "allow_max": 0.5,
      "reject_min": 0.5
    },
    "latency_ms": {
      "gate": 0.02,
      "inference": 11.4,
      "total": 11.6
    },
    "decided_at": "2026-11-02T23:55:00.123+00:00",
    "schema_version": "1.9",
    "request_id": "5b0c7e0e-4321-4def-9abc-123456789abc"
  }
}
```

### 4.3 Moderation Rejected Response (`Action: "reject"`)

**HTTP Status:** `200 OK` (HTTP evaluation succeeded; verdict is rejection)  
**Endpoint:** `POST /v1/moderate`

`Message` and `Note` carry the **policy wording from `config/feedback_messages.yaml`**, not the
submitted text — the content itself is only ever in `Data.content`, and `feedback.issues` carries
character offsets into it. A `.NET` caller can write `Note` straight into an entity's remark
column without echoing a member's own words back at them.

```json
{
  "Success": true,
  "Message": "It appears to break BONC's community policies on fraud and scams. Rewrite the highlighted parts and submit your post again. If you think this is a mistake, contact support.",
  "Type": "warning",
  "Data": {
    "Action": "reject",
    "Decision": "reject",
    "action": "reject",
    "decision": "reject",
    "verdict": "reject",
    "approval_status": "reject",
    "legacy_decision": "reject",
    "Note": "It appears to break BONC's community policies on fraud and scams. Rewrite the highlighted parts and submit your post again. If you think this is a mistake, contact support.",
    "status": "completed",
    "content_id": "POST-5521",
    "content_type": "post",
    "content": "Pay the processing fee to this personal UPI first and I will release your consignment.",
    "content_sha256": "3a8d1e...",
    "risk_score": 0.998,
    "predicted_label": "fraud",
    "label_scores": {
      "fraud": 0.998,
      "spam": 0.001,
      "safe": 0.001
    },
    "gate_matches": [
      {
        "rule_id": "scam.advance_fee",
        "category": "scam",
        "action": "block",
        "spans": [[0, 80]]
      }
    ],
    "feedback": {
      "title": "Your post needs a few changes",
      "message": "Content requests upfront payment or fee transfer outside verified channels.",
      "severity": "high",
      "categories": ["scam"],
      "issues": [
        {
          "start": 0,
          "end": 80,
          "source": "rule",
          "message": "Remove advance payment or unauthorized account instructions.",
          "rule_id": "scam.advance_fee",
          "category": "scam"
        }
      ]
    },
    "gate_version": 2,
    "model_version": "deberta-v3-small-bonc-v9-ver3",
    "thresholds": {
      "allow_max": 0.5,
      "reject_min": 0.5
    },
    "schema_version": "1.9",
    "request_id": "e91c2b44-9988-44aa-bb11-8899aabbccdd"
  }
}
```

### 4.4 Asynchronous 202 Accepted Response (`Action: "pending"`)

**HTTP Status:** `202 Accepted`  
**Endpoint:** `POST /v1/moderate` (When running with `MODERATION_MODE=async`)

```json
{
  "Success": true,
  "Message": "Content queued for asynchronous moderation.",
  "Type": "info",
  "Data": {
    "Action": "pending",
    "status": "pending",
    "request_id": "d7a123e4-55aa-44bb-88cc-0123456789ab",
    "content_id": "ART-9901",
    "content_type": "article",
    "status_url": "/v1/moderate/d7a123e4-55aa-44bb-88cc-0123456789ab",
    "moderation": {
      "content_type": "article",
      "enabled": true,
      "level": "strict"
    }
  }
}
```

### 4.5 Multi-Modal Media Moderation (`POST /v1/moderate/media`)

**HTTP Status:** `200 OK`  
Handles multipart file uploads (`file`, `content_type`, `content_id`).

```json
{
  "Success": true,
  "Message": "Content approved for publication.",
  "Type": "success",
  "Data": {
    "Action": "approved",
    "Decision": "approved",
    "decision": "approved",
    "status": "completed",
    "content_id": "IMG-404",
    "content_type": "article",
    "media": {
      "filename": "factory_machinery.jpg",
      "kind": "image",
      "text_found": true,
      "text_readable": true,
      "ocr_confidence": 0.9942,
      "visual_content_checked": true,
      "visual": {
        "label": "safe",
        "score": 0.987,
        "unsafe_score": 0.013,
        "scores": {
          "safe": 0.987,
          "weapon": 0.005,
          "sexual": 0.004,
          "gore": 0.004
        }
      }
    },
    "risk_score": 0.002,
    "predicted_label": "safe"
  }
}
```

### 4.6 Error Response Contract

**HTTP Status:** `400 Bad Request` / `401 Unauthorized` / `403 Forbidden` / `413 Payload Too Large` / `500 Server Error` / `503 Service Unavailable`

```json
{
  "Success": false,
  "Message": "unknown 'content_type'",
  "Type": "error",
  "Data": null,
  "Error": {
    "code": "invalid_content_type",
    "message": "unknown 'content_type'",
    "allowed": [
      "business_profile",
      "product_listing",
      "post",
      "group_post",
      "advertisement",
      "article",
      "network_article",
      "video",
      "request",
      "proposal",
      "business_proposal"
    ]
  },
  "error": {
    "code": "invalid_content_type",
    "message": "unknown 'content_type'"
  }
}
```

### 4.7 Operational Endpoints (`/health`, `/ready`)

* **Health Probe (`GET /health`):**
  ```json
  {
    "Success": true,
    "Message": "Service is healthy.",
    "Type": "success",
    "Data": {
      "status": "ok"
    }
  }
  ```
* **Readiness Probe (`GET /ready`):**
  ```json
  {
    "Success": true,
    "Message": "Service is ready.",
    "Type": "success",
    "Data": {
      "status": "ready",
      "mode": "sync",
      "model_ready": true,
      "model_version": "deberta-v3-small-bonc-v9-ver3",
      "gate_version": 2,
      "gate_rules": 48,
      "visual": {
        "enabled": true,
        "ready": true,
        "labels": ["safe", "weapon", "sexual", "gore"]
      }
    }
  }
  ```

---

## 5. .NET Microservice Integration Guide & C# Implementation

### 5.1 C# DTO Definitions

Add these DTO definitions to your .NET microservice shared library (`BONC.Shared.Contracts.Moderation`):

```csharp
using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace BONC.Shared.Contracts.Moderation
{
    public class ModerationApiResponse<T>
    {
        [JsonPropertyName("Success")]
        public bool Success { get; set; }

        [JsonPropertyName("Message")]
        public string? Message { get; set; }

        [JsonPropertyName("Type")]
        public string? Type { get; set; }

        [JsonPropertyName("Data")]
        public T? Data { get; set; }
    }

    public class ModerationResultDto
    {
        [JsonPropertyName("Action")]
        public string Action { get; set; } = "reject"; // "approved" or "reject"

        [JsonPropertyName("Status")]
        public string Status { get; set; } = "completed";

        [JsonPropertyName("Note")]
        public string? Note { get; set; }

        [JsonPropertyName("risk_score")]
        public double? RiskScore { get; set; }

        [JsonPropertyName("content_id")]
        public string? ContentId { get; set; }

        [JsonPropertyName("content_type")]
        public string? ContentType { get; set; }

        [JsonPropertyName("feedback")]
        public ModerationFeedbackDto? Feedback { get; set; }
    }

    public class ModerationFeedbackDto
    {
        [JsonPropertyName("title")]
        public string? Title { get; set; }

        [JsonPropertyName("message")]
        public string? Message { get; set; }

        [JsonPropertyName("severity")]
        public string? Severity { get; set; } // "low", "medium", "high"

        [JsonPropertyName("categories")]
        public List<string>? Categories { get; set; }
    }
}
```

### 5.2 HttpClient Integration Pattern

Inject and execute moderation within ASP.NET Core controllers or MediatR handlers:

```csharp
public class ModerationService : IModerationService
{
    private readonly HttpClient _httpClient;
    private readonly ILogger<ModerationService> _logger;

    public ModerationService(HttpClient httpClient, ILogger<ModerationService> logger)
    {
        _httpClient = httpClient;
        _logger = logger;
    }

    public async Task<ModerationResultDto> CheckContentAsync(string content, string contentType, string contentId, CancellationToken ct = default)
    {
        var requestPayload = new
        {
            content = content,
            content_type = contentType,
            content_id = contentId
        };

        var response = await _httpClient.PostAsJsonAsync("/v1/moderate", requestPayload, ct);
        
        if (!response.IsSuccessStatusCode)
        {
            _logger.LogError("Moderation HTTP failure: {StatusCode}", response.StatusCode);
            throw new ModerationException($"Moderation service returned {response.StatusCode}");
        }

        var envelope = await response.Content.ReadFromJsonAsync<ModerationApiResponse<ModerationResultDto>>(cancellationToken: ct);
        
        if (envelope == null || !envelope.Success || envelope.Data == null)
        {
            _logger.LogWarning("Moderation rejected or invalid payload: {Message}", envelope?.Message);
            return new ModerationResultDto 
            { 
                Action = "reject", 
                Note = envelope?.Message ?? "Moderation failure" 
            };
        }

        return envelope.Data;
    }
}
```

### 5.3 Status Transition Workflow

In your entity service (e.g. `ArticleService.cs`):

```csharp
var result = await _moderationService.CheckContentAsync(
    $"{article.Title}\n\n{article.Body}",
    "article",
    article.Id.ToString()
);

if (result.Action == "approved")
{
    article.Status = "Published";
    article.ModerationNote = null;
    await _repository.UpdateAsync(article);
}
else
{
    // Action == "reject"
    article.Status = "Draft"; // or "Rejected"
    article.ModerationNote = result.Note ?? "Content violated safety policies.";
    await _repository.UpdateAsync(article);
    
    // Notify author of required revisions
    await _notificationService.SendModerationNoticeAsync(article.UserId, article.ModerationNote);
}
```

---

## 6. Backward Compatibility & Client SDK Verification

1. **JavaScript Web Client (`moderation-client.js`):**
   * Updated to read `const result = body.Data || body;`.
   * Evaluates `result.decision === "approved" || result.decision === "allow" || result.Action === "approved"`.
2. **Sink & Audit Trail Integrity (`app/sinks.py`):**
   * Emits internal telemetry dictionaries before HTTP JSON serialization.
   * SQL Server moderation audit rows remain 100% compliant with existing DB column constraints.
3. **Automated Test Suite Status:**
   * Full test suite across 42 test modules verified with pytest.
   * All endpoints validated: HTTP status codes (200, 202, 400, 401, 403, 404, 413, 500, 503), headers, and payloads match specifications.
