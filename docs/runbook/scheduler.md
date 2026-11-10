# Runbook: the prospective scheduler on the Mac mini (R1.6)

Run every command from the repository folder (`cd ~/remy-finance`, or wherever you cloned it).

## One-time setup

### 1. Keep the Mac awake on trading days
In **System Settings → Energy**:
- turn on **Prevent automatic sleeping when the display is off**;
- turn on **Start up automatically after a power failure**.

launchd doesn't run jobs while the Mac sleeps. A sleeping Mac produces gaps, which are recorded correctly, but they are lost observations.

### 2. Put the Massive key in the Keychain
launchd jobs don't see your shell's environment variables. Run this command; it prompts for the key, so the key never appears on screen or in your shell history:

```bash
security add-generic-password -s remy-finance -a MASSIVE_API_KEY -w
```

### 3. Make a checkout of the `registry-export` branch
The nightly export is committed there and pushed to GitHub, which gives it an external timestamp.

```bash
git clone --branch registry-export https://github.com/jtsmith509/remy-finance.git ~/remy-registry-export
```

If the branch doesn't exist yet, create it once:

```bash
git clone https://github.com/jtsmith509/remy-finance.git ~/remy-registry-export
```

```bash
cd ~/remy-registry-export && git checkout --orphan registry-export && git rm -rf . && echo "REMY registry export (append-only)" > README.md && git add README.md && git commit -m "registry-export: start" && git push -u origin registry-export && cd -
```

### 4. Check everything
```bash
uv run remy doctor
```

Every line should be `OK`, except `Registry (replay)`, which stays `WARN` until the replay month runs, and `Scheduler heartbeat`, which stays `WARN` until step 5. Fix any `FAIL` before going on.

### 5. Install and start the launchd job
```bash
uv run remy scheduler plist --write --export-repo ~/remy-registry-export
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.remy-finance.tick.plist
```

The job runs `remy run tick` every minute. Each tick does whatever is due and exits.

## Daily
Nothing to do. To see what happened, read today's log:

```bash
cat data/logs/$(date -u +%Y-%m-%d).log
```

Failures also raise a macOS notification and are written under `data/logs/failures/`.

## Stopping and restarting
```bash
launchctl bootout gui/$(id -u)/com.remy-finance.tick
```

To start again, repeat the `launchctl bootstrap` command in step 5.

## Updating the code
Recommendations are skipped while the code has uncommitted changes (DR-0033), so `git pull` and keep the tree clean. Each recommendation records the commit that produced it.

# Runbook: the replay month (R1.8)

The replay runs the same pipeline as the live scheduler over a past month, writing to the **replay** registry. Its results are planning inputs for the power-estimate update (power-estimate.md §5), **not evidence of edge**. It needs the Theta Terminal and the Massive key, and it can run while the scheduler isn't installed. Run every command from the repository folder.

The month must end at least 61 days before today, so that every position opened in it has expired and settled. The tail runs up to 60 days past the month. **August 2026** is the latest month that qualifies (DR-0034).

### 1. Build the universe for the month and the tail months
Each build takes a few minutes. Membership is point-in-time and write-once.

```bash
uv run remy universe build --month 2026-08
```

```bash
uv run remy universe build --month 2026-09
```

```bash
uv run remy universe build --month 2026-10
```

### 2. Run the replay
This takes about 3–4 hours: about 60–70 s per board (measured on the Mac mini, about 170k quotes each), 4 boards per trading day, for the month and the days in its tail that need boards. It's safe to stop and run again, because stored boards are reused and every step is idempotent.

Create the log folder first (`tee` won't create it):

```bash
mkdir -p data/logs
```

```bash
uv run remy replay run --start 2026-08-03 --end 2026-08-31 2>&1 | tee data/logs/replay-2026-08.log
```

### 3. Check it and produce the reports
```bash
uv run remy registry verify --mode replay
```

```bash
uv run remy report power-inputs --mode replay --out data/reports/power-inputs-2026-08.md
```

```bash
uv run remy report cumulative --mode replay --out data/reports/replay-2026-08-cumulative.md
```

Send `data/reports/power-inputs-2026-08.md`, `data/reports/replay-2026-08-cumulative.md` and the last 20 lines of the replay log. The power-estimate update is written from them, as a dated addendum.

## Re-running the replay month (DR-0037)
The first August run used MOVEGEN-1 and MKT-1 v1. To re-run it with the live configuration, run these from the repository folder:

```bash
git pull
```

Archive the first run. Nothing is deleted, and stored boards stay where they are:

```bash
uv run remy replay archive --label 2026-08-movegen1-mkt1v1
```

```bash
uv run remy replay run --start 2026-08-03 --end 2026-08-31 2>&1 | tee data/logs/replay-2026-08-v2.log
```

It reuses every stored board through 16 October, so most of the time goes on recommendations (about 30 s per decision board) and management. The log shows a timing line per decision board. Then:

```bash
uv run remy registry verify --mode replay
```

```bash
uv run remy report power-inputs --mode replay --out data/reports/power-inputs-2026-08-v2.md
```

```bash
uv run remy report cumulative --mode replay --out data/reports/replay-2026-08-v2-cumulative.md
```

```bash
uv run remy replay diagnose --date 2026-08-03 --session open
```

### Third run: MKT-1/RAND-1 v3 (DR-0038)
The same steps with new labels. Archive the v2 run first:

```bash
git pull
```

```bash
uv run remy replay archive --label 2026-08-movegen2-mkt1v2
```

```bash
uv run remy replay run --start 2026-08-03 --end 2026-08-31 2>&1 | tee data/logs/replay-2026-08-v3.log
```

```bash
uv run remy registry verify --mode replay
```

```bash
uv run remy report cumulative --mode replay --out data/reports/replay-2026-08-v3-cumulative.md
```

```bash
uv run remy report power-inputs --mode replay --out data/reports/power-inputs-2026-08-v3.md
```

```bash
uv run remy replay diagnose --date 2026-08-03 --session open > data/reports/diagnose-2026-08-03-v3.txt
```
