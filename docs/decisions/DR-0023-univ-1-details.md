# DR-0023: UNIV-1 implementation details and the option-volume read path

- **Status:** accepted (confirmed by Josh, 2026-11-08, at Stop Point B)
- **Date:** 2026-11-08
- **Source:** Handoff §5 R0.5; refines DR-0004 (accepted)
- **Stage:** R0

## Decision
1. **New observation, `OptionVolume`:** total option contracts traded for an underlying on one session, from the vendor's end-of-day report. It's available (`public_time`) at **17:15 ET** on that session; ThetaData generates EOD reports at 17:15 ET (R0.1).
2. **Extension to the data protocol:** `MarketDataSource.option_volume(underlying, session_date)`. This adds to the six methods in handoff R0.3. `PointInTimeView.option_volume` raises `LookaheadError` if asked for the `as_of` session or a later one, or if a returned record is public after `as_of`. ThetaData implements it via `option/history/eod?expiration=*`, one request per (symbol, session).
3. **Selection time:** the first XNYS session of each month, with `as_of` at **09:00 ET**. That's before the open snapshot, and before any data from the selection session itself exists.
4. **Window:** the 20 sessions ending the previous session.
5. **Score:** the average daily contracts over the sessions that have data. A name needs at least **15 of 20** sessions with data. Otherwise it is ineligible, and the reason is recorded.
6. **Ranking:** descending average, with ties broken by symbol. The top 50 are members.
7. **Record:** `UniverseDecision` holds the rule and candidate-list versions, the selection session, `as_of`, the window, every candidate's score and rank (or reason for ineligibility), the members, and `inputs_hash`. That is the SHA-256 over the content hashes of every `OptionVolume` used.
8. **Storage:** decisions are stored write-once at `data/universe/UNIV-1/<selection_session>.json` and set read-only.
9. **Membership in force:** for a session, it's the latest decision with `selection_session ≤ session`. Boards built from it record the universe version `UNIV-1:<selection_session>`.

## Consequences
- **No mid-month change.** A name that delists mid-month stays a member until the next selection. Its missing data then shows up as gap notes on the boards.
- **Bootstrapping a replay month** takes about 80 × 20 = 1,600 EOD requests, then about 80 a day. That's fine at four concurrent requests.
- **Remaining survivorship bias:** the 80-name candidate list was chosen in 2026 (DR-0004). Every replay report must state this.
