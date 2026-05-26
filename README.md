# misarmy talkbot

Discord TTS bot. The package lives under `src/misarmy_talkbot`.

Voice playback is delegated to [Lavalink](https://lavalink.dev/) via
[wavelink](https://github.com/PythonistaGuild/Wavelink). TTS is still generated
in the bot; only transport and decoding run in Lavalink. Generated audio is
written to a shared `tmpfs` volume that Lavalink loads by absolute path.

## Why Lavalink and wavelink (not discord.py voice)

The bot used to drive voice with an in-process `VoiceClient` and FFmpeg
(py-cord). That couples the Discord gateway, the voice WebSocket, FFmpeg
subprocesses, and playback state in one Python process. In practice that meant
custom recovery loops, close-code handling, and fragile restarts when any layer
stalled.

**Lavalink** is a separate audio node: it owns the voice protocol, buffering,
and Lavaplayer decoding. The bot stays a thin control plane (connect, enqueue
tracks, react to track events). **wavelink** is the discord.py-oriented client
for Lavalink v4 REST/WebSocket; we use stock `discord.py` plus wavelink rather
than maintaining a fork for voice.

Tradeoffs we accepted:

- **Extra service** — a JVM Lavalink container in Compose; ops cost is one more
  process, gain is isolating audio from the bot event loop.
- **Local files via tmpfs** — TTS MP3s live on a volume mounted in both
  containers; Lavalink loads `local` sources by path (no bot-side HTTP server).
- **Less bot-side voice logic** — no in-process FFmpeg for playback, no voice
  health gate or close-code state machine; session resume and transport issues
  are Lavalink/wavelink concerns.

TTS generation (gTTS, ffmpeg post-process for MP3) remains in Python; only
playback moved out of process.

## Run it (Docker)

I run it in a container, not directly on the host. Copy [`.env.example`](.env.example) to `.env`, set `DISCORD_TOKEN`, and see [Environment](#environment) for other variables.

### Development (build bot image locally)

```bash
mkdir -p logs/lavalink
docker compose up --build
```

### Production (pull bot from GHCR)

After the image is published from `main` (see [GitHub Actions](#github-actions)):

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

On the prod server, to avoid typing `-f` every time:

```bash
export COMPOSE_FILE=docker-compose.prod.yml
```

Add that to `~/.bashrc` or a small script you source before `docker compose up`. Private GHCR packages require `docker login ghcr.io` once on that host.

Both compose files start two services:

- `lavalink` — Java audio node (image `ghcr.io/lavalink-devs/lavalink:4`).
- `misarmy_talkbot` — the bot (`docker-compose.yml` builds locally; `docker-compose.prod.yml` uses `ghcr.io/misdocumeno/misarmy-talkbot:latest`).

Detached (dev):

```bash
docker compose up --build -d
docker compose logs -f misarmy_talkbot
```

Logs:

- **Development:** bind-mount `./logs` (bot → `/data/talkbot.log`, Lavalink → `./logs/lavalink/`). Handy for `tail` and the VS Code debug `LOG_FILE` path.
- **Production:** named volume `misarmy-talkbot-logs` (bot and Lavalink share it; no host `chown` — the container user owns files on first write). Tail with `docker compose logs -f …` (stdout). To read the rotating file on the volume: `docker run --rm -v <project>_misarmy-talkbot-logs:/data alpine tail -f /data/talkbot.log` (adjust volume name from `docker volume ls`).

## Development

Clone the repo, copy `.env.example` to `.env`, then install tooling with [Poetry](https://python-poetry.org/) if you want to edit Python locally:

| Task | Command |
|------|---------|
| Install | `poetry install` |
| Install git hooks (format before commit) | `sh scripts/install-githooks.sh` |
| Format + fix imports | `poetry run ruff format src tests && poetry run ruff check --fix src tests` |
| Lint | `poetry run ruff check src tests` |
| Typecheck | `poetry run pyright src tests` |
| Test | `poetry run pytest` |

**Ruff** (`ruff.toml`): format (79 cols, single quotes, preview) + lint (Ruff defaults plus strict annotations). **Pyright** (`pyrightconfig.json`, strict): real type checking in the editor (via a Pyright-based language server) and on the CLI.

Production images install dependencies at build time and run `python -m misarmy_talkbot` (no Poetry at runtime). See `Dockerfile`. `.dockerignore` keeps `.env`, `config/`, and other local-only paths out of the build context even if they exist on disk.

### GitHub Actions

| Workflow | When it runs | What it does |
|----------|----------------|--------------|
| **CI** | Pull requests and pushes to `main` | `ruff format --check`, `ruff check`, `pyright`, `pytest` |
| **Publish Docker image (GHCR)** | After **CI succeeds** on a push to `main`, or manual dispatch | Builds the image and pushes to `ghcr.io/<owner>/<repo>` |

Opening a pull request into `main` runs **CI** only (no image publish). After merge, **CI** runs on `main`; **Publish** starts only if that run passes (not in parallel).

### Dev container (optional)

For editing and running tools inside a consistent Linux environment (Poetry, ffmpeg, Ruff, Pyright), open the repo in a [Dev Container](https://containers.dev/): **Cursor / VS Code** command palette → **Dev Containers: Reopen in Container**. Uses `.devcontainer/` only; production `Dockerfile` and `docker compose` are unchanged.

After the container builds, `poetry install` runs once via `postCreateCommand`. Put `.env` in the repo root as usual. Running the bot for a real smoke test is still `docker compose up --build` from the host.

## Architecture (voice path)

Per-guild flow:

1. Slash command or follow event hits `LavalinkSession.ensure_connected_to(channel_id)`.
2. The bot calls `channel.connect(cls=wavelink.Player, self_deaf=True)` (or `move_to`).
3. For each text message, `PlaybackEngine.enqueue(audio)` schedules `audio.process()` as a background task; TTS bytes are written to `${AUDIO_DIR}/<uuid>.mp3` (a tmpfs volume mounted in both containers).
4. The speak loop waits for the head of the queue to reach `READY`, then loads the file via `wavelink.Pool.fetch_tracks('/tmp/talkbot-audio/<uuid>.mp3')` (plain absolute path; do not use `Playable.search` with `file://` or Lavalink will treat it as a YouTube Music query) and calls `LavalinkSession.play_track()` (minimal PATCH without disabled filters).
5. The loop blocks on `_track_done` (an `asyncio.Event` set by `on_wavelink_track_end` / `on_wavelink_track_exception`); no polling, no FFmpeg in the bot process for playback.
6. After track end, the bot deletes the MP3; the `AudioStorage` janitor sweeps anything orphaned by crashes.

There is no voice recovery loop, health gate, or close-code state machine. Lavalink and wavelink own voice transport stability.

## Configuration

Config is JSONC on disk (comments allowed), validated at load time with Pydantic.

| File | Scope |
|------|--------|
| `config/global.jsonc` | Defaults for all guilds (locale, voice, replacements, …). Lives on the mounted config volume at runtime (gitignored locally). |
| `config/guilds/<guild_id>.jsonc` | Per-guild overrides (optional; created via `/config`) |
| [`src/misarmy_talkbot/infra/config/default_config.jsonc`](src/misarmy_talkbot/infra/config/default_config.jsonc) | Shipped template returned by `/config default` |

**Presence** (global only): sidebar subtitle under the bot name.

- **Default text** comes from the gettext id `presence_default_name` in bundled locale files (`src/misarmy_talkbot/infra/locale/*/LC_MESSAGES/messages.po`). Edit and recompile `.mo` (or use a custom locale under `config/locales/`) to change the default line.
- **Per-guild / global string override** without editing `.po`: `localeOverrides` with key `presence_default_name` in `global.jsonc` or a guild config.
- **Optional** `presence` block in `global.jsonc` only if you need a non-translated custom `name`, a non-default `type`, or `streaming` + `url`:

```jsonc
"presence": {
  "type": "playing",
  "name": "Literal sidebar text (skips gettext)"
}
```

Omit `presence` entirely for translated default + `type: playing`. `type` values: `playing`, `listening`, `watching`, `competing`, `streaming` (`url` required for streaming).

**localeOverrides**: map gettext message ids to custom strings for that guild (or global). See `localeOverrides` in the default config template.

Restart the bot after changing `global.jsonc` or locale files so presence reloads on the next boot.

### Custom locales (production-friendly)

If you want to maintain a custom translation without rebuilding the image, add a
new locale under the mounted config directory:

```
config/locales/<your-locale>/LC_MESSAGES/messages.po
```

On startup, the container compiles `messages.po` → `messages.mo` (requires
`msgfmt`, shipped in the image) and adds `<your-locale>` to `/locales`.

Optional: add a fallback chain so you only need to translate the strings you
care about:

```
config/locales/<your-locale>/fallback
```

Put a base locale code in that file (e.g. `es_AR`), and missing strings will
fall back to the base locale.

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

## Docker + IDE debugger

Debug overlay (bind-mounts source, enables debugpy, DEBUG logs):

```bash
mkdir -p logs/lavalink
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
```

In VS Code, Cursor, or any editor with a [debugpy](https://github.com/microsoft/debugpy) attach config, use the **Docker: Attach to Talkbot** launch configuration (F5) after the bot is online. Path mapping: local `src/misarmy_talkbot/` → container `/home/bot/src/misarmy_talkbot`. Tokens come from `.env` via compose `env_file`.

The attach port is optional and never blocks startup or the event loop.
