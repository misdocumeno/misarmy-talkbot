# misarmy talkbot

Discord TTS bot. The package lives under `src/misarmy_talkbot`.

## Run it (Docker)

I run it in a container, not directly on the host. You need a `.env` with `DISCORD_TOKEN` (see [Environment](#environment)).

```bash
mkdir -p logs
docker compose up --build
```

Detached:

```bash
docker compose up --build -d
docker compose logs -f misarmy_talkbot
```

Logs land in `./logs/talkbot.log` on the host (bind-mounted from `/data/talkbot.log` in the container). See [Docker (debug logs on the host)](#docker-debug-logs-on-the-host) for volumes and config.

## Development

Clone the repo, create `.env`, then install tooling with [Poetry](https://python-poetry.org/) if you want to edit Python locally:

| Task | Command |
|------|---------|
| Install | `poetry install` |
| Format + fix imports | `poetry run ruff format src tests && poetry run ruff check --fix src tests` |
| Lint | `poetry run ruff check src tests` |
| Typecheck | `poetry run pyright src tests` |
| Test | `poetry run pytest` |

**Ruff** (`ruff.toml`): format (79 cols, single quotes, preview) + lint (Ruff defaults plus strict annotations). **Pyright** (`pyrightconfig.json`, strict): real type checking in the editor (via a Pyright-based language server) and on the CLI. I use the [Ruff VS Code extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) for format-on-save; any editor with Ruff + Pyright support works the same way.

Production images install dependencies at build time and run `python -m misarmy_talkbot` (no Poetry at runtime). See `Dockerfile`.

### Dev container (optional)

For editing and running tools inside a consistent Linux environment (Poetry, ffmpeg, Ruff, Pyright), open the repo in a [Dev Container](https://containers.dev/): **Cursor / VS Code** command palette → **Dev Containers: Reopen in Container**. Uses `.devcontainer/` only; production `Dockerfile` and `docker compose` are unchanged.

After the container builds, `poetry install` runs once via `postCreateCommand`. Put `.env` in the repo root as usual. Running the bot for a real smoke test is still `docker compose up --build` from the host (or from a terminal in the dev container if Docker socket is available).

In a dev container, Cursor can run terminal commands with fewer approval prompts because the agent works inside the container sandbox. That is a common workflow; it does not replace production compose.

## Branch status: `feature/lifecycle-overhaul` (dead end)

This branch is a **best-effort fix** for voice playback and reconnect behavior using **py-cord / discord.py `VoiceClient` + in-process FFmpeg**. I am **not** merging it to `main` on purpose.

### What I tried

- Serialize voice connect/move/disconnect through a per-guild pilot and recovery supervisor.
- Replace fixed 90s playback waits with budgets derived from compressed TTS size.
- Treat missing `after` callbacks and stale `is_playing` as completion where possible.
- Debounce recoveries and clear stuck `gateway_transient` flags after long idle.

### Why it still fails in practice

In-process voice is fragile for an always-on talkbot:

- **`VoiceClient.play` + FFmpeg in the bot process** - completion signals (`after`, `is_playing`) are unreliable; the queue logic has to guess when audio ended.
- **Voice WebSocket lifecycle** - session invalidation, region moves, and idle drops surface as gateway `voice_state_update` events; reconnecting does not always restore stable playback timing.
- **Coupled gateway and voice health** - a brief main-gateway blip can leave flags that block TTS for minutes unless every path clears them.

I validated that a **separate stack without in-bot FFmpeg** (Lavalink in another project) stayed up for multi-day runs. That is the direction for the next branch. This README does not document Lavalink itself; it only records that **this branch is frozen** and that **migration replaces py-cord voice**, not more patches on top of it.

### If you run this branch

Use the teardown logs (`voice_teardown_initiated`, `voice_bot_left_channel`, `voice_close_code_received`) to see whether a drop was initiated by the bot or reported by Discord without a recent teardown line.

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | (required) | Bot token |
| `LOG_LEVEL` | `INFO` | Terminal log level |
| `LOG_FILE` | (empty) | If set, rotating DEBUG log path (e.g. `/data/talkbot.log` in Docker) |
| `LOG_FILE_MAX_BYTES` | `52428800` | Rotation size (bytes; supports `50MiB` style) |
| `LOG_FILE_BACKUP_COUNT` | `5` | Rotated file count |
| `FORENSICS_ENABLED` | `false` | Extra diagnostics |
| `GRACE_DROP_SECONDS` | `60` | Voice disconnect grace before auto-unfollow |
| `WS_WATCHDOG_INTERVAL_SECONDS` | `5` | Voice recovery supervisor poll |
| `WS_UNHEALTHY_GRACE_SECONDS` | `5` | Unhealthy backstop before forced reconnect |
| `WS_LIBRARY_GRACE_MS` | `1500` | Let library retry before forcing reconnect |
| `WS_4014_WAIT_MS` | `1500` | Wait for server update after close 4014 |
| `WS_RATE_LIMIT_BACKOFF_MS` | `5000` | Backoff for rate limits / 4022 |
| `VOICE_CONNECT_TIMEOUT` | `30` | Voice connect timeout (seconds) |
| `OPS_ANNOUNCE_COOLDOWN_SECONDS` | `300` | Min seconds between error replies per (guild, user) |
| `OPS_ANNOUNCE_RECOVERY_THRESHOLD` | `3` | (reserved) consecutive recovery failures before announce |
| `METRICS_SNAPSHOT_INTERVAL_SECONDS` | `300` | (reserved) periodic metrics log interval |

## Docker (debug logs on the host)

Create a local log directory (gitignored), then build and run:

```bash
mkdir -p logs
docker compose up --build
```

Detached:

```bash
docker compose up --build -d
docker compose logs -f misarmy_talkbot
```

Logs are written inside the container at `/data/talkbot.log` and appear on the host as `./logs/talkbot.log` (and rotated siblings `talkbot.log.1`, ...). Compose sets `LOG_LEVEL=DEBUG` and `LOG_FILE=/data/talkbot.log`; override via `.env` if needed.

Equivalent without Compose:

```bash
mkdir -p logs
docker build -t misarmy_talkbot .
docker run --rm -it \
  --env-file .env \
  -e LOG_LEVEL=DEBUG \
  -e LOG_FILE=/data/talkbot.log \
  -v "$(pwd)/logs:/data" \
  -v misarmy-talkbot-config:/home/bot/config \
  misarmy_talkbot
```

Named config volume keeps DB/presets under `/home/bot/config` as before. Only `./logs` is bind-mounted for easy tailing during smoke tests.

## Docker + IDE debugger

Debug overlay (bind-mounts source, enables debugpy, `FORENSICS_ENABLED`, DEBUG logs):

```bash
mkdir -p logs
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
```

In VS Code, Cursor, or any editor with a [debugpy](https://github.com/microsoft/debugpy) attach config, use the **Docker: Attach to Talkbot** launch configuration (F5) after the bot is online. Path mapping: local `src/misarmy_talkbot/` > container `/home/bot/src/misarmy_talkbot`. Tokens come from `.env` via compose `env_file`.

The attach port is optional and never blocks startup or the event loop (Discord will time out interactions if the process waits on a debugger).

### Control-flow tracing (no debugger needed)

Every step logs as `TRACE seq=N engine.fn PHASE ...` with monotonic `seq` so you can diff two runs line-by-line.

```bash
grep TRACE logs/talkbot.log | tail -120
```

**Working message** - you should see a full chain ending in:

`LOOP` > `wait_head EXIT` > `dequeue EXIT` > `play_call EXIT` > `play_after_callback` > `play_wait_after EXIT` > `LOOP_END outcome='played_ok'`

**Broken / deaf bot** - find the first `ENTER` without a matching `EXIT`, or `engine.stuck_watch STUCK`:

| Stuck after | Meaning |
|-------------|---------|
| `queue.wait_head POINT blocked` (no `EXIT`) | Head never becomes READY - TTS stuck |
| `engine.wait_healthy ENTER` (no `EXIT`) | Gateway health gate (`gateway_transient`) |
| `pilot.wait_playback_idle ENTER` (no `EXIT`) | Reconnect blocked on playback |
| `engine.play_wait_after ENTER` (no `play_after_callback`) | discord `after` never fired |
| `engine.stuck_watch STUCK` | Speak loop stopped ticking but queue still has work |
| No more `engine.speak_loop LOOP` | Speak task crashed - search `speak_task_crash` |

`FORENSIC` lines add full queue snapshots on each loop (`FORENSICS_ENABLED=true` in compose).
