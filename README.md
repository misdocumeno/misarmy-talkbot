# misarmy talkbot

Discord TTS bot. The package lives under `src/misarmy_talkbot`.

Voice playback is delegated to [Lavalink](https://lavalink.dev/) via
[wavelink](https://github.com/PythonistaGuild/Wavelink); the bot does **not**
run an in-process `VoiceClient` or its own FFmpeg playback. Generated TTS
audio is written to a shared `tmpfs` volume that Lavalink loads via `file://`.

## Run it (Docker)

I run it in a container, not directly on the host. You need a `.env` with `DISCORD_TOKEN` (see [Environment](#environment)).

```bash
mkdir -p logs
docker compose up --build
```

Compose starts two services:

- `lavalink` — Java audio node (image `ghcr.io/lavalink-devs/lavalink:4`).
- `misarmy_talkbot` — the bot itself, depends on Lavalink reporting healthy.

Detached:

```bash
docker compose up --build -d
docker compose logs -f misarmy_talkbot
```

Logs land in `./logs/talkbot.log` on the host (bind-mounted from `/data/talkbot.log` in the container).

## Development

Clone the repo, create `.env`, then install tooling with [Poetry](https://python-poetry.org/) if you want to edit Python locally:

| Task | Command |
|------|---------|
| Install | `poetry install` |
| Format + fix imports | `poetry run ruff format src tests && poetry run ruff check --fix src tests` |
| Lint | `poetry run ruff check src tests` |
| Typecheck | `poetry run pyright src tests` |
| Test | `poetry run pytest` |

**Ruff** (`ruff.toml`): format (79 cols, single quotes, preview) + lint (Ruff defaults plus strict annotations). **Pyright** (`pyrightconfig.json`, strict): real type checking in the editor (via a Pyright-based language server) and on the CLI.

Production images install dependencies at build time and run `python -m misarmy_talkbot` (no Poetry at runtime). See `Dockerfile`.

### Dev container (optional)

For editing and running tools inside a consistent Linux environment (Poetry, ffmpeg, Ruff, Pyright), open the repo in a [Dev Container](https://containers.dev/): **Cursor / VS Code** command palette → **Dev Containers: Reopen in Container**. Uses `.devcontainer/` only; production `Dockerfile` and `docker compose` are unchanged.

After the container builds, `poetry install` runs once via `postCreateCommand`. Put `.env` in the repo root as usual. Running the bot for a real smoke test is still `docker compose up --build` from the host.

## Architecture (voice path)

Per-guild flow:

1. Slash command or follow event hits `LavalinkSession.ensure_connected_to(channel_id)`.
2. The bot calls `channel.connect(cls=wavelink.Player, self_deaf=True)` (or `move_to`).
3. For each text message, `PlaybackEngine.enqueue(audio)` schedules `audio.process()` as a background task; TTS bytes are written to `${AUDIO_DIR}/<uuid>.mp3` (a tmpfs volume mounted in both containers).
4. The speak loop waits for the head of the queue to reach `READY`, then loads the file via `wavelink.Pool.fetch_tracks('/tmp/talkbot-audio/<uuid>.mp3')` (plain absolute path; do not use `Playable.search` with `file://` or Lavalink will treat it as a YouTube Music query) and calls `player.play(track)`.
5. The loop blocks on `_track_done` (an `asyncio.Event` set by `on_wavelink_track_end` / `on_wavelink_track_exception`); no polling, no FFmpeg in the bot process for playback.
6. After track end, the bot deletes the MP3; the `AudioStorage` janitor sweeps anything orphaned by crashes.

There is no voice recovery loop, health gate, or close-code state machine. Lavalink and wavelink own voice transport stability.

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `DISCORD_TOKEN` | (required) | Bot token |
| `LAVALINK_HOST` | `lavalink` | Hostname of the Lavalink node (compose service name in Docker) |
| `LAVALINK_PORT` | `2333` | Lavalink HTTP/WebSocket port |
| `LAVALINK_PASSWORD` | `youshallnotpass` | Shared password between Lavalink and the bot |
| `AUDIO_DIR` | `/tmp/talkbot-audio` | Shared tmpfs path for generated TTS files |
| `AUDIO_TTL_SECONDS` | `600` | Janitor TTL for orphaned audio files |
| `AUDIO_JANITOR_INTERVAL_SECONDS` | `120` | Janitor sweep interval |
| `TTS_MAX_CONCURRENT` | `4` | Per-guild bound on concurrent TTS generation tasks |
| `LOG_LEVEL` | `INFO` | Terminal log level |
| `LOG_FILE` | (empty) | If set, rotating DEBUG log path (e.g. `/data/talkbot.log` in Docker) |
| `LOG_FILE_MAX_BYTES` | `52428800` | Rotation size (bytes; supports `50MiB` style) |
| `LOG_FILE_BACKUP_COUNT` | `5` | Rotated file count |
| `GRACE_DROP_SECONDS` | `60` | Voice disconnect grace before auto-unfollow |
| `OPS_ANNOUNCE_COOLDOWN_SECONDS` | `300` | Min seconds between error replies per (guild, user) |
| `METRICS_SNAPSHOT_INTERVAL_SECONDS` | `300` | Periodic metrics log interval |

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
docker compose logs -f lavalink
```

Logs are written inside the container at `/data/talkbot.log` and appear on the host as `./logs/talkbot.log`. Compose sets `LOG_LEVEL=INFO` and `LOG_FILE=/data/talkbot.log`; override via `.env` if needed.

## Docker + IDE debugger

Debug overlay (bind-mounts source, enables debugpy, DEBUG logs):

```bash
mkdir -p logs
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
```

In VS Code, Cursor, or any editor with a [debugpy](https://github.com/microsoft/debugpy) attach config, use the **Docker: Attach to Talkbot** launch configuration (F5) after the bot is online. Path mapping: local `src/misarmy_talkbot/` → container `/home/bot/src/misarmy_talkbot`. Tokens come from `.env` via compose `env_file`.

The attach port is optional and never blocks startup or the event loop.

## Logs to watch

For a healthy boot you should see:

```
lavalink_pool_connect_initiated host=lavalink port=2333
lavalink_node_ready node=main session_id=... resumed=...
Logged in as <bot> (guilds=...)
```

Per-message playback logs:

```
lavalink_connected guild_id=<g> channel_id=<c>
tts_ready content=... bytes=... path=/tmp/talkbot-audio/...mp3
```

If a track failed:

```
speak_track_failed guild_id=<g> reason=...
```

If Lavalink is unreachable, the bot logs `lavalink_pool_connect_failed` and continues to run; commands that need voice will fail until the node is back.
