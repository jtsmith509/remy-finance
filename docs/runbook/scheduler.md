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
