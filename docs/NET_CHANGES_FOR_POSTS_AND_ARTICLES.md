# What to change in the .NET server: moderation for posts and articles

**Audience:** the backend engineer who owns the ASP.NET Core API.
**Scope:** posts and articles only — `/api/Post`, `/api/Article`, `/api/NetworkArticle`.
Group posts are a three-line extension of the same work; everything else (listings,
proposals, business profiles, videos) is out of scope for this phase.

The moderation service is a separate Python service on the AI/ML host. It holds the models
and the policy; it does not touch your database. **You call it, you get a verdict, then you
decide what to write.** It is a check in front of your publish path, not a replacement for
any of it.

---

## 1. The contract

**Base URL:** the AI/ML host (ask for the final hostname; it is not on `api.boncnetwork.com`).
**Auth:** `Authorization: Bearer <SERVICE_TOKEN>` — the same scheme your Swagger already
declares as `Bearer` (an `apiKey` in the `Authorization` header), so your existing handler
works unchanged. The token will be issued to you.

### Request — `POST /v1/moderate`

```json
{
  "content": "Title\n\nBody text of the article…",
  "content_type": "article",
  "content_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

| Field | Rules |
|---|---|
| `content` | Required, non-empty string. **Max 25,000 characters** or you get `413` with code `content_too_long`. |
| `content_type` | Required. For this phase: `post`, `article`, `network_article` (and `group_post`). It selects the wording the author sees — it does not change how strictly text is scored. |
| `content_id` | Optional, string or integer. **Send your entity's Id.** It is stored in the moderation audit log, which is the only way to tie a verdict back to a row later. |

### Response — always `200` for a verdict

```json
{
  "Success": true,
  "Message": "Content approved for publication.",
  "Data": {
    "Action": "approved",
    "Note": null,
    "legacy_decision": "allow",
    "request_id": "b1e2…",
    "decided_by": "model",
    "risk_score": 0.08,
    "feedback": null
  }
}
```

It is the `ApiResponse<T>` envelope you already use: `Success`, `Message`, `Data`, and
nothing else. **The payload is in `Data`.**

| Field in `Data` | What it is |
|---|---|
| `Action` | **The verdict. Branch on this.** `"approved"` or `"reject"`. |
| `Note` | The author-facing reason, ready for a remark column. Policy wording, around 200 characters, **never the member's own text**. `null` when approved. |
| `legacy_decision` | `"allow"` / `"revise"` / `"reject"`. Worth storing: `Action` folds *revise* into *reject*, and this is the only way to tell "asked to fix a highlighted part" from a flat refusal. |
| `request_id` | The moderation audit row. Store it — it is how a decision gets explained after the fact. |
| `feedback` | `{title, message, severity, issues[]}` when refused. `issues[]` carries character offsets into the exact `content` string you sent, for highlighting. Ignore it if you are not showing highlights. |

### ⚠️ The misread to avoid

**`Success: true` does not mean approved.** It means the HTTP call worked. A refusal is a
successful call that returns `Action: "reject"` with `200`.

```csharp
if (response.Success) Publish();              // WRONG — publishes rejected content
if (IsApproved(response.Data.Action)) Publish();   // right
```

`Success: false` is reserved for 4xx/5xx — a malformed request, a bad token, the service
being unwell. Then `Data.code` is the stable machine-readable reason (`invalid_content`,
`invalid_content_type`, `content_too_long`, `unauthorized`, `model_not_ready`) and `Message`
is prose that may be reworded.

---

## 2. Two decisions I need from you before this ships

**These are the whole risk. Everything else here is plumbing.**

### 2.1 What exact strings should `Action` be?

Your Swagger declares `AdminArticleActionDto.Action` and `ArticleAdminActionDto.Action` as
`{"type": "string", "nullable": true}` — no enum, no pattern. So the vocabulary is not
written down anywhere I can read.

We currently send lowercase `"approved"` and `"reject"`. But the only two string-valued
vocabularies in all 641 of your endpoints are PascalCase:

- `LoginOtpChannel` → `Sms`, `Email`
- `RespondToB2BProposalDTO.ResponseType` → `^(Accept|Reject|Message)$`

So lowercase may well be wrong. **Tell us the values your handler expects and we will send
exactly those** — it is a config change on our side, not a release.

### 2.2 What does your code do with an `Action` value it does not recognise?

This matters more than the spelling:

```csharp
if (action == "approved") Publish(); else KeepDraft();   // mismatch → nothing publishes. Annoying, obvious, safe.
if (action == "reject") KeepDraft(); else Publish();     // mismatch → PUBLISHES REJECTED CONTENT. Silent.
```

Under the second shape, a casing mismatch does not stall the integration — it quietly
defeats moderation while every dashboard looks healthy.

**Please write the compare so that anything which is not exactly the approve token is
treated as not-approved**, regardless of what we agree in 2.1:

```csharp
private static bool IsApproved(string? action) =>
    string.Equals(action, ModerationOptions.ApprovedToken, StringComparison.OrdinalIgnoreCase);
```

That way a mismatch fails closed and someone notices within minutes, instead of it being
found later in published content.

---

## 3. The change, step by step

### 3.1 Configuration

```jsonc
// appsettings.json
"Moderation": {
  "BaseUrl": "https://moderation.internal.boncnetwork.com",
  "ServiceToken": "",          // from a secret store / env, not source control
  "TimeoutSeconds": 10,        // text is fast; see §5 before raising it for images
  "ApprovedToken": "approved", // whatever we settle in §2.1
  "FailOpen": false            // see §3.4 — leave false
}
```

### 3.2 A typed client

```csharp
builder.Services.AddHttpClient<IModerationClient, ModerationClient>((sp, http) =>
{
    var o = sp.GetRequiredService<IOptions<ModerationOptions>>().Value;
    http.BaseAddress = new Uri(o.BaseUrl);
    http.Timeout     = TimeSpan.FromSeconds(o.TimeoutSeconds);
    http.DefaultRequestHeaders.Authorization =
        new AuthenticationHeaderValue("Bearer", o.ServiceToken);
});
```

### 3.3 DTOs

```csharp
public sealed class ModerationEnvelope<T>
{
    [JsonPropertyName("Success")] public bool Success { get; set; }
    [JsonPropertyName("Message")] public string? Message { get; set; }
    [JsonPropertyName("Data")]    public T? Data { get; set; }
}

public sealed class ModerationResult
{
    // Defaults to reject so a payload that fails to bind, or a field the service stops
    // sending, fails closed rather than publishing unchecked content.
    [JsonPropertyName("Action")]           public string Action { get; set; } = "reject";
    [JsonPropertyName("Note")]             public string? Note { get; set; }
    [JsonPropertyName("legacy_decision")]  public string? LegacyDecision { get; set; }
    [JsonPropertyName("request_id")]       public string? RequestId { get; set; }
    [JsonPropertyName("decided_by")]       public string? DecidedBy { get; set; }
    [JsonPropertyName("risk_score")]       public double? RiskScore { get; set; }

    // NOTE: these keys are snake_case and System.Text.Json is case-sensitive by default.
    // `[JsonPropertyName("Status")]` or `[JsonPropertyName("LegacyDecision")]` would bind
    // nothing and silently keep the default — which for Action means "reject everything".
}

public sealed class ModerationError
{
    [JsonPropertyName("code")]    public string? Code { get; set; }
    [JsonPropertyName("message")] public string? Message { get; set; }
}
```

### 3.4 The client, failing closed

```csharp
public async Task<ModerationVerdict> CheckAsync(
    string content, string contentType, string? contentId, CancellationToken ct)
{
    try
    {
        var res = await _http.PostAsJsonAsync("/v1/moderate",
            new { content, content_type = contentType, content_id = contentId }, ct);

        if (!res.IsSuccessStatusCode)
        {
            var err = await res.Content.ReadFromJsonAsync<ModerationEnvelope<ModerationError>>(ct);
            _log.LogError("moderation refused the request: {Status} {Code} {Message}",
                (int)res.StatusCode, err?.Data?.Code, err?.Message);
            return ModerationVerdict.Unavailable;   // NOT an approval
        }

        var ok = await res.Content.ReadFromJsonAsync<ModerationEnvelope<ModerationResult>>(ct);
        if (ok?.Data is null) return ModerationVerdict.Unavailable;

        return new ModerationVerdict(
            Approved:  IsApproved(ok.Data.Action),
            Note:      ok.Data.Note,
            RequestId: ok.Data.RequestId,
            Decision:  ok.Data.LegacyDecision);
    }
    catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
    {
        _log.LogError(ex, "moderation service unreachable");
        return ModerationVerdict.Unavailable;
    }
}
```

**`Unavailable` must not publish.** If moderation cannot be reached, hold the item as a draft
and tell the author it is still being checked. Publishing unchecked content because a network
call failed is the one outcome this whole service exists to prevent — and an outage would
turn into a flood of it. Our browser client already fails closed this way.

If you need an escape hatch, make it the `FailOpen` flag above, default `false`, so turning
it on is a deliberate, visible decision rather than the default behaviour on a bad day.

### 3.5 Where to call it

**Write the row as a draft first, then moderate, then flip the status.** You already have the
levers for this, so it needs no new state machine:

| Surface | Create | Status lever |
|---|---|---|
| Post | `POST /api/Post/createPost` (`CreatePostDTO`) | `PUT /api/Post/saveAsDraftPost`, `PUT /api/Post/publishPost` |
| Article | `POST /api/Article/CreateArticle` (`CreateArticleDto` — already takes `Status`) | `PATCH /api/Article/UpdateStatus/{articleId}` |
| Network article | `POST /api/NetworkArticle/CreateArticle` (`CreateNetworkArticleDto`) | `PUT /api/NetworkArticle/UpdateStatus/{articleId}` |

Draft-first is worth the extra write:

- `content_id` can be the real entity Id, so the moderation audit row ties to the row.
- A refused attempt leaves a record you can show the author and review later.
- If the process dies mid-flight the item is a draft, not a published unknown.

**Also moderate on edit.** `PUT /api/Article/UpdateArticle/{articleId}`,
`PUT /api/NetworkArticle/UpdateArticle/{articleId}` and `PUT /api/Post/updatePost` all let a
member replace clean text with something else after approval. An edit to a published item
should be re-checked, and should not go live until it passes. `POST /api/Post/republishPost`
needs the same treatment.

### 3.6 What text to send

Join the member-written fields into **one** request. Harm is routinely split across fields —
a clean title with the scam in the body is still a scam, and checking them separately misses
it.

| `content_type` | Join these, in this order |
|---|---|
| `post` | `TextContent`, then `Hashtags` |
| `article` | `Title`, then `Body`, then `Hashtags` |
| `network_article` | `Title`, then `Body`, then `Hashtags` |

Join with a blank line (`"\n\n"`), and **join the same way every time**: the offsets in
`Data.feedback.issues` index into the exact string you sent, so if you ever want per-field
highlighting the mapping has to be reproducible.

Watch the 25,000-character limit. An article with a 20,000-character body fits comfortably;
anything larger comes back `413 content_too_long` rather than being silently truncated, so
decide what you want to do with it (truncating means the untruncated tail is unchecked).

### 3.7 Store the result

On the entity, or a side table:

- `ModerationRequestId` — ties to our audit log.
- `ModerationDecision` — `legacy_decision`, so *revise* stays distinguishable from *reject*.
- `ModerationNote` — `Note`, shown to the author.
- `ModeratedAtUtc`.

### 3.8 Forward the member's IP, or the blocklist stops working

This one is easy to miss and it breaks an existing feature.

Today a member's browser calls the moderation service directly, so each request arrives from
that member's own address. The service records it on every audit row, and the admin dashboard
has a "blocked clients" screen that refuses submissions from an address or network.

**Once you are the only caller, every request arrives from the .NET server's IP.** Two things
follow:

- Audit rows all record the same address, so the log can no longer show where a decision came
  from.
- Blocking becomes all-or-nothing. An operator can no longer block one abusive member — and
  if anyone ever blocks that one address or a network containing it, **every submission on the
  platform starts failing** with `403 client_blocked`.

**The fix is on your side:** send the member's real address in `X-Forwarded-For` on each call.

```csharp
req.Headers.Add("X-Forwarded-For", httpContext.Connection.RemoteIpAddress?.ToString());
```

We then set `TRUST_PROXY_HOPS=1` so the service reads the member's address from that header
instead of the socket. It only trusts the header because the caller is authenticated by the
bearer token — which is the other reason §2's token is not optional.

If you would rather not forward it, say so and we will turn the blocklist screen off rather
than leave an admin feature that looks like it works and does not.

---

## 4. Acceptance checks

Run these against a test environment before enabling it on live traffic.

1. **Clean article publishes.** Ordinary business text → `Action: "approved"` → status goes
   to published, `Note` is null.
2. **Scam text does not publish.** Try `"Pay a small registration fee to join and earn 50000 per week from home."`
   → `Action: "reject"` → item stays draft, `Note` stored and shown.
3. **A refusal is not an error.** Confirm case 2 returned HTTP `200` with `Success: true`,
   and that your code did not treat it as a success-to-publish. This is the §1 misread.
4. **Wrong token.** Point `ServiceToken` at rubbish → `401`, `Success: false`,
   `Data.code == "unauthorized"` → item stays draft.
5. **Service down.** Stop the moderation service → item stays draft, author told it is still
   being checked, nothing published. **If anything publishes here, stop and fix it.**
6. **Casing.** Whatever we settle in §2.1, send the opposite casing once and confirm the item
   is *not* published. That is the §2.2 guarantee, verified rather than assumed.
7. **Edit path.** Publish a clean article, edit it to scam text, confirm the edit does not go
   live.

---

## 5. Images — phase 2, not now

Posts and articles carry `MediaPaths` / `Medias` / `ThumbnailUrl`. Pictures are checked
separately, by `POST /v1/moderate/media` as `multipart/form-data` with a `file` part plus
`content_type` and `content_id` form fields. It reads any words in the picture (OCR, English
and Devanagari) and classifies the image itself.

Two reasons it is not in this phase:

- It needs the **image bytes**, not a storage URL, so you would be fetching each upload back
  out of storage to forward it.
- It is slower than text — OCR plus a vision model — so the timeout in §3.1 is wrong for it.
  Give image checks their own, longer timeout rather than raising the text one.

Text moderation on posts and articles is worth shipping on its own first.

---

## 6. Questions back to us

1. The exact `Action` strings (§2.1) — and please confirm the compare is written per §2.2.
2. `Note`: free text, and what column length? Ours run to roughly 200 characters.
3. `AdminJobActionDTO` takes `Status` + `Note` where the article DTOs take `Action` + `Note`.
   Out of scope here, but if jobs are coming later, is the vocabulary the same set?
4. Where do you want the bearer token stored, and can the .NET host reach the ML host?
5. Will you forward `X-Forwarded-For` (§3.8)? If not, we will disable the blocked-clients
   screen rather than ship one that silently cannot do its job.
