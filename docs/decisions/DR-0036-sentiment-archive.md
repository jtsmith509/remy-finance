# DR-0036: A narrow prospective archive of public text (proposal only; not built)

- **Status:** accepted as the framework (confirmed by Josh, 2026-11-09). **Not built.** Sources: SEC EDGAR, Massive ticker news and Fed/Treasury releases. Re-checks at +1 h, +24 h and +7 d. Building and capture start **after Stop Point C**, as the first post-R1 step; each source is switched on only after a recorded terms check.
- **Date:** 2026-11-09
- **Source:** Josh's request, 2026-11-09 (task 2 of the R0 amendment prompt). Related: DR-0017 (time model), DR-0022/0024 (write-once stores and gaps), DR-0012 (backup), DR-0026 (data sources), EVAL-1.
- **Stage:** R1 (capture only); feature extraction is deferred to a later stage

## Context
A future spec (v0.6) may use public text (filings, news, social posts) as an input to forecasts. Such data is only useful for honest prospective evaluation if it is captured **as it appears, when it appears**, with every item kept, because historical text archives are revised, deleted, backfilled and survivorship-biased. Capturing it alongside the R1 market snapshots starts that clock early and cheaply. Nothing in R1 reads it: no model, baseline or recommendation.

## 1. Source set: small, fixed, versioned (`TEXT-SOURCES-1`; Josh decides)
The list below is a **placeholder of candidates**, not a selection. Terms and prices were checked on 2026-11-09, mostly through third-party summaries, and **each must be confirmed against the provider's own terms before a source is switched on.** Only lawful, public sources are allowed: no login-walled scraping, no paywall circumvention, and no material nonpublic information.

| Candidate | What | Terms / licence (as found) | Cost | Rate limits | Notes |
|---|---|---|---|---|---|
| **SEC EDGAR** (filings index, RSS, full-text documents) | 8-K, 10-Q/K, 13D/G, Form 4 for universe names | US government public data. Automated access must declare a User-Agent with contact details. | Free | SEC's published fair-access maximum is **10 requests/s**; exceeding it gets throttled or blocked | The strongest candidate: authoritative, timestamped, revision history visible (amendments) |
| **Massive ticker news** (`/v2/reference/news`) | headline, summary, publisher, URL, tickers, `published_utc` | Under our existing Massive subscription terms | Believed to be on the current (free) plan: **verify on the Mac**; massive.com wasn't reachable from the cloud session | free plan about 5 requests/min (already throttled in our adapter) | Summaries and links only, not full articles. Publisher article bodies are **not** fetched (publisher terms). |
| **Benzinga news via Massive** (`/benzinga/v2/news`) | full-text, timestamped financial news | A separate Massive "Benzinga expansion" plan | Paid, price not published in what I found: **ask Massive** | per plan | Full text under licence; check the redistribution and storage terms |
| **Federal Reserve / Treasury press releases** (RSS) | FOMC statements, minutes, speeches | US government public information | Free | polite polling | Macro context, tiny volume |
| **GDELT DOC 2.0 API** | global news-article index (URL, title, time, tone) | The GDELT Project describes its data as free for any academic, commercial or governmental use (verify on gdeltproject.org). "GDELT Cloud" is a separate commercial product with stricter terms. | Free, no key | undocumented; poll gently | Article URLs only; publisher terms govern the articles themselves |
| **Reddit Data API** (a few finance subreddits, cashtag matches) | posts and comments | Free tier for eligible **non-commercial** use, which now needs approval. Commercial terms aren't self-serve. Reddit's data terms restrict use for AI/ML; **verify before any model use**. | Free if approved | **100 queries/min per OAuth client** (10 unauthenticated) | Deletions and edits are common, so revision tracking matters most here |
| **X (Twitter) API** (cashtag search for universe names) | posts | X developer agreement; pay-per-use since February 2026 | about **$0.005 per post read** (about $5 per 1,000); 1,000 reads/day ≈ $150/month | a monthly read cap (2–3 M, sources disagree) | Expensive per item; consider only if Josh wants social data and accepts the cost |
| **Stocktwits** | cashtag message streams | Official API access is restricted or by approval; third-party wrappers are of unclear legality | unknown | unknown | **Not recommended** unless official access is granted |

**Rule:** a source is switched on only after Josh's choice, a recorded check of its current terms (saved with the source version), and a `doctor` check that its credentials are present. Credentials follow CLAUDE.md rule 6 (environment or Keychain). Changing the set creates `TEXT-SOURCES-2`; items keep the version they were captured under.

## 2. What is stored for every item (no filtering on outcome)
Every item returned by a source's query for the universe is kept, including items followed by no market move. No relevance, quality or outcome filter is applied at capture. Filtering is a later, versioned, documented feature step.

- **`TextItem`**, written once at first sight, as a frozen record with canonical JSON and a SHA-256 hash (as for all records):
  - source and source version, the source's item ID, and the URL;
  - the query that found it (for example, the cashtag, or the CIK and form type);
  - the universe membership version in force;
  - the **source's publication time**, as given (possibly revised later);
  - **first-seen time** (`ingest_time`: when our poll received it);
  - raw payload (the API response bytes, unparsed) and extracted raw text;
  - content hash;
  - capture metadata: poller version, request, HTTP status and response headers relevant to caching, and the poll-cycle ID.
- **`TextObservation`**, appended on every later re-fetch:
  - item ID, observation time, status (`PRESENT`, `EDITED`, `DELETED`, `UNAVAILABLE`);
  - content hash, and the new raw text if changed;
  - the source's revision or edit time, if given.

  Re-fetch schedule: each item is re-observed at about +1 h, +24 h and +7 d. Revisions, edits and deletions are never applied in place. Each is a new observation, so every version the archive ever saw stays readable.
- **Availability for point-in-time use** (DR-0017): an item is available at its **first-seen time**, never at the source's publication time, which can be backdated or revised. A version of the text is available at the observation time that first recorded it.

## 3. Feature extraction (deferred; rules fixed now)
- **Not built now.** Language-model reading or any other extraction is a later stage.
- **Every future feature record must carry:** the model name and version (an exact model ID), the full prompt or template and its hash, parameters, the input item and version hashes, the code commit, and the **processing timestamp**.
- **Prospective versus retrospective:**
  - A feature counts as **prospective** only if it was computed and frozen (written to an append-only store with its processing timestamp) **before** both the decision that uses it and the outcome it is scored against.
  - **Any later reprocessing** (new model, new prompt, re-run over old items) is labelled **retrospective**. It may be studied, but under EVAL rules it is never mixed with, or substituted for, prospective features. This mirrors the prospective/replay separation of the registry.
- **Look-ahead rule:** feature inputs are restricted to item versions observed by the decision time, through a `PointInTimeView`-style guard. A feature computed later from a version first seen before the decision is still retrospective.

## 4. Storage, backup and gaps
- **Layout:** a write-once, content-addressed store, like snapshots:
  - `data/text/<source>/<YYYY-MM-DD>/items-<poll-cycle>.jsonl.zst` and `observations-<poll-cycle>.jsonl.zst`;
  - raw payload blobs under `data/text/blobs/<sha256>`;
  - each file published atomically and read-only (`publish_file`), with a per-day manifest of file hashes.
- **Git:** `data/` stays git-ignored. Text isn't committed (licence and size).
- **Backup:** nightly with the registry backup (DR-0012), to `data/backup/` for cloud sync. The per-day manifests' hashes are added to the nightly registry export, which gives the archive the same external timestamp.
- **Capture schedule:** polled by the scheduler tick (DR-0033). Every 5 minutes from 07:00 to 20:00 ET on trading days, hourly otherwise, within each source's rate limit. Each poll writes a `PollRecord` (source, start, end, cursor, items found, status).
- **Gaps:** a failed or missed poll leaves the interval between the last successful poll's end and the next successful poll's start **uncovered**. That interval is written as a `TextGap` record and a scheduler notification is raised.
  - Items found later whose publication time falls inside a gap are stored with their **true, later first-seen time** and the flag `published_during_gap`.
  - They are **never backfilled** with an earlier availability, so they can't leak into decisions made during the gap.
  - Gaps are never filled from a historical archive into the prospective record. A historical archive, if ever bought, goes into a separate store labelled retrospective.

## 5. Volume and cost estimate (for a 50-name universe)
| Source | Items/day (rough) | Raw size/day | Compressed (zstd, about 4×) | Cost |
|---|---|---|---|---|
| EDGAR (filings for universe names plus RSS) | 5–30 documents | 5–20 MB (10-Q/K bodies are large) | 1–5 MB | $0 |
| Massive ticker news | 200–800 | 0.5–2 MB | 0.2–0.5 MB | $0 if on the current plan (verify) |
| Fed and Treasury releases | 0–5 | < 0.5 MB | < 0.1 MB | $0 |
| GDELT DOC (filtered to universe names) | 500–3,000 index rows | 1–3 MB | 0.3–1 MB | $0 |
| Reddit (a few subreddits) | 1,000–5,000 posts and comments | 5–20 MB | 1–5 MB | $0 if approved |
| X cashtag search (optional) | 1,000 reads | about 2 MB | about 0.5 MB | about $150/month |
| Re-observations (+1 h, +24 h, +7 d) | about 3× items | mostly hashes; text only when changed | small | — |

**Total: about 2–12 MB/day compressed, roughly 1–4 GB a year**, well within the Mac mini's disk. **Cost is $0** with the free and government sources. Optional paid additions are X (about $150/month at 1,000 reads/day) and Benzinga full text (Massive expansion plan; price to confirm).

## Consequences
- **Clean prospective evidence.** Text is captured honestly from the first day, with first-seen times, revisions and gaps, so a later text-based engine can be tested prospectively without hindsight. Nothing in R1 depends on it.
- **Operating cost:** one more thing for the scheduler to poll, plus a `doctor` check per source.
- **Legal and licensing risk sits at the source level,** which is why each source is switched on only after a recorded terms check, and full article text is stored only where a licence allows it.

## Questions for Josh (answered 2026-11-09)
1. **Which sources?** My suggestion for a first, low-risk set: **EDGAR, Massive ticker news, and Fed releases**, which are free, public and authoritative. Add Reddit and GDELT only after a terms review, and X only if the cost is acceptable. **EDGAR, Massive ticker news and Fed/Treasury releases.**
2. **Timing:** should capture start with the 5-day live run, or after Stop Point C? **After Stop Point C.**
3. **Re-observation schedule** (+1 h, +24 h, +7 d): acceptable, or do you want denser re-checks for social sources, where deletions happen fast? **+1 h, +24 h, +7 d.**
