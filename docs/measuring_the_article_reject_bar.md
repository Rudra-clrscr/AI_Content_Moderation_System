# Measuring the article reject bar at `level_1`

**Date:** 2026-11-01 · **Model:** `bonc-v9-ver3` · **Instance:** local, 8,483 audit rows

A clean technology article was refused publication. The model had not got it wrong — it scored
the article `safe` at 89.3% confidence and matched no gate rule. It was refused because the
`article` bar moved from `0.5` to `0.1` and the document scored `0.106997`.

This note is the measurement behind that, for whoever owns the per-surface levels. **It does
not recommend a bar.** `level_1` across every surface is recorded here as deliberate; what
follows is what that bar costs, so the trade is made with numbers rather than by feel.

---

## 1. The refusal

`id=8483`, `content_id a96d1fd2-b13d-4df0-a09c-fd06a8441330`, 2,322 characters, an explainer
titled *"The Rise of Physical AI: Humanoid Robots and the Next Industrial Frontier"*.

| | |
|---|---|
Model's own label | `safe`, 89.3% |
Gate matches | none |
Whole-document score | **0.106997** |
`reject_min` | **0.100000** |
Decision | `reject`, `decided_by: model` |
Margin | **0.007** |

At the bar `article` carried earlier the same day (`level_5`, `reject_min: 0.5`) this article
publishes. The change happened inside a 75-minute window: a read of the levels table at
**05:42:50Z** reported `article` at `level_5`/`0.5` with every other surface at `level_1`, and
the refusal is stamped **06:57:52Z**, by which time all eleven surfaces read `level_1`/`0.1`.

## 2. No sentence in it is near the bar

The sentence scan ran over all 18 spans. Every one, highest first:

```
0.008955  The Autonomous Supply Chain: The line between software and physical task execution…
0.008766  Bridging the gap between digital intelligence and physical execution has been a…
0.004345  Varied and Unstructured Environments: Unlike static factory arms, humanoid robots…
0.003961  For years, AI existed purely in the digital realm—generating text, analyzing data…
0.003593  Physics and Action Simulation: Millions of iterations are practiced in physics-based…
0.002807  Real-Time Adaptation: On-board AI processors run neural networks that allow the robot…
0.001861  Executive Summary
0.001495  At the core of Physical AI is a shift away from pre-programmed trajectories…
0.001081  Today, the most significant frontier is Physical AI: embedding complex Vision-Language…
0.000830  Compute at the Edge: Training these systems requires vast simulated environments…
0.000590  Labor Support in Hazardous Roles: Industries from logistics to heavy manufacturing…
0.000407  The Challenge: From Screens to Physical Space
0.000358  Hardware-Software Convergence: Value is rapidly moving toward platforms…
0.000291  Perceptual Foundation: Large-scale vision-language models capture and understand 3D…
0.000203  Market Impact
0.000193  The Rise of Physical AI: Humanoid Robots and the Next Industrial Frontier
0.000185  The Architecture: Vision-Language-Action (VLA) Models
0.000124  Sensory-Motor Integration: Translating abstract instructions into precise, real-time…
```

**Highest sentence 0.008955. Whole document 0.106997 — twelve times higher than any part of
it.** The word scan produced zero candidates.

### What the author was shown

The feedback highlighted *"For years, AI existed purely in the digital realm—generating text,
analyzing data, and automating software workflows"* and told them to rewrite it so it is
"factual, specific and respectful". That sentence scores **0.003961** on its own — ranked
fourth, 25× under the bar. Submitted alone it scores 0.001223 and is allowed.

The highlighter had no offending span to point at, because there isn't one, so it surfaced the
best of a uniformly clean set. **An author asked to rewrite that sentence cannot succeed**:
nothing they do to it will move a document-level score it contributes almost nothing to.

## 3. What the bar costs on real traffic

1,144 article decisions the audit log records as `allow`, bucketed by `risk_score`:

| risk band | count | share | at `reject_min: 0.1` |
|---|---|---|---|
| 0 – 0.01 | 972 | 85.0% | still allowed |
| 0.01 – 0.05 | 110 | 9.6% | still allowed |
| 0.05 – 0.1 | 20 | 1.7% | still allowed |
| **0.1 – 0.2** | **25** | **2.2%** | **refused** |
| **0.2 – 0.3** | **3** | **0.3%** | **refused** |
| **0.3 – 0.5** | **1** | **0.1%** | **refused** |
| 0.5 – 1.0 | 13 | 1.1% | refused |

**42 of 1,144 (3.7%)** carry a score at or above the current bar. **29 of them (2.5%)** sit in
the `0.1`–`0.5` band — the population the move from `level_5` to `level_1` converted from allow
to reject. Roughly **one article in forty** that used to publish no longer does.

**Read this as a score distribution, not a clean before-and-after.** These rows were decided
under whatever thresholds were live at the time, which is why 13 of them scored above 0.5 and
were allowed anyway. What the table supports is the shape: the mass sits under 0.01, there is a
thin tail, and moving the bar from 0.5 to 0.1 sweeps in about 2.5% of article traffic. It does
not establish that all 29 are false positives — reading them is the next step, and
`content_id` plus `raw_result.content` are in the log for exactly that.

## 4. Two findings that outlive whatever the bar is

### A. The whole-document score is 12× its own highest sentence

`decided_by: model` means the document-level pass is what refuses. On this article that pass
returns 0.107 while nothing inside it exceeds 0.009.

Worth knowing before trusting any bar in the 0.1–0.2 region: if long-form prose systematically
scores above its own parts, then **article length pushes content toward the bar independently
of what the content says**, and a tightened bar hits long articles first. The 256-token window
on the whole-text pass is the obvious first thing to check — it sees only the opening of a
2,322-character document.

Not investigated here. It would want scoring a corpus of known-clean articles at several
lengths and comparing the document score against the maximum sentence score.

### B. A refused article tells its author the problem is minor

```
decision : reject
severity : low
title    : "Your article can't be published"
message  : "A small thing to look at before this goes out."
```

`severity_of()` in `app/feedback.py` bands on the score — `high` at 0.80, `medium` from 0.50,
`low` below — while the decision comes from the threshold. The two agreed while `reject_min`
sat at 0.5 or above. At 0.1 they cannot: **every refusal scoring between 0.1 and 0.5 is
reported to the author as `low`**, which is the entire new reject band. The dashboard renders
that as "Moderation rating LOW · A small thing to look at before this goes out" directly above
"This article hasn't been published."

That is not an edge case at this bar, it is the common case. Deriving severity from the
distance over the threshold rather than from fixed score bands would hold at any bar.

## 5. Reproducing

The article is in the audit log; `raw_result.content` carries the full text.

```python
row = GET /v1/admin/moderation-logs?limit=1&surface=article&decision=reject
text = row["logs"][0]["raw_result"]["content"]
POST /v1/moderate  {"content": text, "content_type": "article"}
```

The distribution in section 3 is the same endpoint with `decision=allow`, paged in 250s to
1,500, reading `risk_score` off each row.

## 6. Resolution of Findings A & B

Both architectural defects identified in Section 4 have been resolved:

### Resolution of Finding A (Highlighter False Attribution)
In `app/pipeline.py` (`_sentence_issues`):
- Previously, when a document was rejected as an aggregate whole by `Stage.MODEL`, but no individual sentence crossed `thresholds.allow_max`, the highlighter fell back to `[max(scored, key=lambda x: x[2])]`. This falsely blamed and highlighted innocent sentences (e.g. score 0.003961, 25x below threshold) and generated meaningless trigger words.
- **Fixed:** For `Decision.REJECT`, `_sentence_issues` only selects sentences that actually crossed `thresholds.allow_max` (`chosen = flagged`). When all sentences are clean, `issues` remains empty. The author is informed of the document-level policy rejection without falsely accusing innocent sentences.

### Resolution of Finding B (Severity Mismatch on Strict Rejections)
In `app/feedback.py` (`severity_of`) and `app/pipeline.py`:
- Previously, `severity_of` mapped any score `< 0.50` to `"low"`, so content rejected at `level_1` (0.10), `level_2` (0.20), or `strict` (0.25) was reported with `"severity": "low"` ("A small thing to look at before this goes out") directly under a rejection notice.
- **Fixed:** `severity_of(risk, decision, threshold=threshold)` now accounts for the decision and threshold. A rejection is **never** rated `"low"`. If content is rejected, it is rated at least `"medium"`, and reaches `"high"` when risk is substantially above threshold (`risk >= threshold + 0.30` or `risk >= 0.80`).

