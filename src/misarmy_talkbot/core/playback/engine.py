"""Per-guild speak loop driven by Lavalink track events.

Two cooperating concerns:

1. ``enqueue`` spawns a background TTS task per message; generation overlaps with
   playback so a long current track does not delay future TTS work. A per-guild
   semaphore bounds the concurrent TTS workload.
2. ``_speak_loop`` waits for the head of the queue to reach ``READY``, joins the
   master's voice channel via Lavalink, and asks the player to play the HTTP URL.
   It blocks on ``_track_done`` (set by ``on_track_end`` / ``on_track_exception``).
   Stall detection (no auto-skip) lives in the observability stall watcher and
   uses Lavalink position progress, not a blind wall-clock timeout.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING

import discord
import wavelink

from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.playback.queue import MessageQueue
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.tracing import speak_span

if TYPE_CHECKING:
    from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
    from misarmy_talkbot.core.playback.audio import AudioMessage
    from misarmy_talkbot.core.voice.lavalink_session import LavalinkSession
    from misarmy_talkbot.observability.metrics import MetricsRegistry


class PlaybackEngine:
    """Owns the per-guild TTS queue and Lavalink playback orchestration."""

    def __init__(
        self,
        guild_id: int,
        lavalink: LavalinkSession,
        announcer: ErrorReplyAnnouncer,
        *,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.guild_id = guild_id
        self._lavalink = lavalink
        self._announcer = announcer
        self._metrics = metrics
        self._queue = MessageQueue(guild_id)
        self._speak_task: asyncio.Task[None] | None = None
        self._gen_tasks: set[asyncio.Task[None]] = set()
        # message_id → in-flight TTS task (cancelled on edit/delete).
        self._gen_by_message: dict[int, asyncio.Task[None]] = {}
        # Keep this low: Edge TTS returns intermittent 429 under chat bursts.
        max_concurrent = max(1, int(os.getenv('TTS_MAX_CONCURRENT', '2')))
        self._gen_semaphore = asyncio.Semaphore(max_concurrent)
        self._stopped = False
        self._current: AudioMessage | None = None
        self._track_done = asyncio.Event()
        self._track_failure: str | None = None
        # Stall watcher reads these (progress heartbeat from Lavalink updates).
        self.play_started_monotonic: float | None = None
        self.last_position_ms: int = 0
        self.last_progress_monotonic: float | None = None
        self.current_track_length_ms: int | None = None

    def _master_voice_channel(
        self,
    ) -> discord.VoiceChannel | discord.StageChannel | None:
        master_user_id = FollowRegistry.instance().master(self.guild_id)
        if master_user_id is None:
            return None
        guild = self._lavalink._guild()
        if guild is None:
            return None
        member = guild.get_member(master_user_id)
        if (
            member is None
            or member.voice is None
            or member.voice.channel is None
        ):
            return None
        return member.voice.channel

    def start(self) -> None:
        if self._speak_task is None or self._speak_task.done():
            self._speak_task = asyncio.create_task(
                self._supervised_speak(), name=f'speak_{self.guild_id}'
            )

    async def shutdown(self) -> None:
        self._stopped = True
        # Wake the speak loop if it is parked on _track_done.
        self._track_done.set()
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass
        for task in list(self._gen_tasks):
            if not task.done():
                task.cancel()
        if self._gen_tasks:
            await asyncio.gather(*self._gen_tasks, return_exceptions=True)
        await self._cleanup_all_queued()

    async def _cleanup_all_queued(self) -> None:
        items = list(self._queue)
        await self._queue.clear()
        for item in items:
            await item.cleanup()

    async def enqueue(self, audio: AudioMessage) -> None:
        if self._stopped:
            return
        flags = getattr(audio.original, 'flags', None)
        forwarded = bool(
            getattr(flags, 'forwarded', False) if flags is not None else False
        )
        logger.info(
            'enqueue guild_id=%s user_id=%s message_id=%s depth=%s '
            'forwarded=%s content=%r',
            self.guild_id,
            getattr(audio.original.author, 'id', None),
            audio.original.id,
            len(self._queue) + 1,
            forwarded,
            (audio.content or '')[:120],
        )
        await self._queue.put(audio)
        if self._metrics:
            self._metrics.inc('messages_enqueued_total', self.guild_id)
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
        self._spawn_generation(audio)

    def _spawn_generation(self, audio: AudioMessage) -> None:
        """Start (or replace) the TTS task for ``audio.original.id``."""
        message_id = audio.original.id
        previous = self._gen_by_message.get(message_id)
        if previous is not None and not previous.done():
            previous.cancel()

        task = asyncio.create_task(
            self._process_audio(audio), name=f'tts_{self.guild_id}'
        )
        self._gen_tasks.add(task)
        self._gen_by_message[message_id] = task

        def _on_done(done: asyncio.Task[None]) -> None:
            self._gen_tasks.discard(done)
            if self._gen_by_message.get(message_id) is done:
                self._gen_by_message.pop(message_id, None)

        task.add_done_callback(_on_done)

    async def _process_audio(self, audio: AudioMessage) -> None:
        async with self._gen_semaphore:
            try:
                with speak_span(
                    'tts',
                    guild_id=self.guild_id,
                    message_id=audio.original.id,
                ):
                    await audio.process()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    'tts_unhandled guild_id=%s content=%r',
                    self.guild_id,
                    audio.content,
                )
                audio.state = AudioState.FAILED
        if audio.state == AudioState.FAILED:
            if self._metrics:
                self._metrics.inc('tts_failures_total', self.guild_id)
            try:
                await self._announcer.announce(
                    audio.original,
                    'tts',
                    'The text-to-speech step failed for this message.',
                )
            except Exception:
                logger.exception(
                    'tts_announce_failed guild_id=%s', self.guild_id
                )
            await self._queue.remove(audio)
            await audio.cleanup()
            if self._metrics:
                self._metrics.set_gauge(
                    'queue_depth', self.guild_id, float(len(self._queue))
                )
        else:
            notice_msgid = audio.fallback_notice_msgid
            if notice_msgid:
                try:
                    from misarmy_talkbot.infra.locale import translate

                    await self._announcer.announce(
                        audio.original,
                        'tts_fallback',
                        translate(notice_msgid, audio.original.guild),
                    )
                except Exception:
                    logger.exception(
                        'tts_fallback_announce_failed guild_id=%s',
                        self.guild_id,
                    )
            await self._queue.notify_state_change()

    async def clear(self) -> None:
        await self._cleanup_all_queued()
        if self._metrics:
            self._metrics.set_gauge('queue_depth', self.guild_id, 0.0)

    async def on_message_edit(self, message: discord.Message) -> None:
        """Regenerate TTS for a queued (not yet playing) message.

        The item currently in ``_current`` is left alone: mid-play rewrite would
        race Lavalink. Only the waiting queue is updated.
        """
        for queued in self._queue:
            if queued.original == message:
                if queued is self._current:
                    return
                await queued.edit(message)
                self._spawn_generation(queued)
                break

    async def on_message_delete(self, message: discord.Message) -> None:
        """Drop a queued message; leave the currently playing track alone."""
        for queued in list(self._queue):
            if queued.original == message:
                if queued is self._current:
                    return
                previous = self._gen_by_message.get(message.id)
                if previous is not None and not previous.done():
                    previous.cancel()
                await self._queue.remove(queued)
                await queued.cleanup()
                if self._metrics:
                    self._metrics.set_gauge(
                        'queue_depth', self.guild_id, float(len(self._queue))
                    )
                break

    async def stop(self, member: discord.Member) -> tuple[discord.Colour, str]:
        member_in_queue = [
            item for item in self._queue if item.original.author == member
        ]
        current_is_member = (
            self._current is not None
            and self._current.original.author == member
        )
        if not member_in_queue and not current_is_member:
            return discord.Colour.red(), 'not_talking'
        for item in member_in_queue:
            await self._queue.remove(item)
            await item.cleanup()
        if current_is_member:
            await self._skip_current(reason='stop_member')
        if self._metrics:
            self._metrics.inc('messages_skipped_total', self.guild_id)
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def stop_all(self) -> tuple[discord.Colour, str]:
        if self._current is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'
        await self._cleanup_all_queued()
        await self._skip_current(reason='stop_all')
        if self._metrics:
            self._metrics.set_gauge('queue_depth', self.guild_id, 0.0)
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def skip(self, member: discord.Member) -> tuple[discord.Colour, str]:
        target = (
            self._current if self._current is not None else self._queue.head()
        )
        if target is None:
            return discord.Colour.red(), 'not_talking'
        if (
            target.original.author != member
            and not member.guild_permissions.mute_members
        ):
            return discord.Colour.red(), 'skip_no_permission'
        if self._current is target:
            await self._skip_current(reason='skip')
        else:
            await self._queue.remove(target)
            await target.cleanup()
        if self._metrics:
            self._metrics.inc('messages_skipped_total', self.guild_id)
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def _skip_current(self, *, reason: str) -> None:
        player = self._lavalink.player
        if player is None:
            return
        try:
            await player.skip(force=True)
        except Exception:
            logger.exception(
                'skip_failed guild_id=%s reason=%s', self.guild_id, reason
            )

    def on_track_end(self, _payload: wavelink.TrackEndEventPayload) -> None:
        if self._metrics:
            self._metrics.inc('lavalink_track_end_total', self.guild_id)
        self._clear_play_progress()
        self._track_done.set()

    def on_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        self._track_failure = (
            repr(payload.exception)
            if getattr(payload, 'exception', None) is not None
            else 'exception'
        )
        if self._metrics:
            self._metrics.inc('lavalink_track_exception_total', self.guild_id)
        self._clear_play_progress()
        self._track_done.set()

    def on_track_stuck(
        self, _payload: wavelink.TrackStuckEventPayload
    ) -> None:
        self._track_failure = 'stuck'
        if self._metrics:
            self._metrics.inc('lavalink_track_stuck_total', self.guild_id)
        self._clear_play_progress()
        self._track_done.set()

    def on_player_update(self, position_ms: int) -> None:
        """Lavalink progress heartbeat; stall watcher uses last_progress_monotonic."""
        if self._current is None:
            return
        if position_ms > self.last_position_ms:
            self.last_position_ms = position_ms
            self.last_progress_monotonic = time.monotonic()
            if self._metrics:
                self._metrics.set_gauge(
                    'speak_position_ms', self.guild_id, float(position_ms)
                )

    def _clear_play_progress(self) -> None:
        self.play_started_monotonic = None
        self.last_position_ms = 0
        self.last_progress_monotonic = None
        self.current_track_length_ms = None
        if self._metrics:
            self._metrics.set_gauge(
                'speak_blocked_seconds', self.guild_id, 0.0
            )
            self._metrics.set_gauge('speak_position_ms', self.guild_id, 0.0)

    def note_progress_for_stall(self) -> float | None:
        """Seconds since last position advance while a track is current, else None."""
        if self._current is None or self.last_progress_monotonic is None:
            return None
        return max(0.0, time.monotonic() - self.last_progress_monotonic)

    @property
    def queue(self) -> MessageQueue:
        return self._queue

    @property
    def current(self) -> AudioMessage | None:
        return self._current

    @property
    def gen_task_count(self) -> int:
        return sum(1 for task in self._gen_tasks if not task.done())

    def has_gen_task_for(self, message_id: int) -> bool:
        task = self._gen_by_message.get(message_id)
        return task is not None and not task.done()

    async def drop_head(self) -> bool:
        """Operator recover: drop current queue head (or playing item)."""
        head = (
            self._current if self._current is not None else self._queue.head()
        )
        if head is None:
            return False
        if self._current is head:
            await self._skip_current(reason='recover_drop_head')
            return True
        previous = self._gen_by_message.get(head.original.id)
        if previous is not None and not previous.done():
            previous.cancel()
        await self._queue.remove(head)
        await head.cleanup()
        if self._metrics:
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
        return True

    async def restart_speak_loop(self) -> None:
        """Cancel and recreate the speak task; keep queue and follow state."""
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
            try:
                await self._speak_task
            except asyncio.CancelledError:
                pass
        self._stopped = False
        self._track_done.set()
        self._track_done.clear()
        self._current = None
        self._clear_play_progress()
        self.start()
        await self._queue.notify_state_change()

    async def _supervised_speak(self) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                await self._speak_loop()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('speak_task_crash guild_id=%s', self.guild_id)
                if self._metrics:
                    self._metrics.inc(
                        'speak_task_restarts_total', self.guild_id
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _speak_loop(self) -> None:
        while not self._stopped:
            audio = await self._queue.wait_until_head(AudioState.READY)
            if self._stopped:
                return
            await self._play_one(audio)

    async def _play_one(self, audio: AudioMessage) -> None:
        channel = self._master_voice_channel()
        if channel is None:
            # Master is inside the disconnect grace window: keep speaking
            # where the bot already is instead of dropping everyone's messages.
            player = self._lavalink.player
            channel = player.channel if player is not None else None
        if channel is None:
            logger.warning(
                'speak_no_master guild_id=%s; dropping head', self.guild_id
            )
            await self._drop_head(audio)
            return

        error = await self._lavalink.ensure_connected_to(channel.id)
        if error is not None:
            logger.warning(
                'speak_connect_failed guild_id=%s err=%s',
                self.guild_id,
                error,
            )
            await self._announcer.announce(
                audio.original, 'voice', 'Could not join voice channel.'
            )
            await self._drop_head(audio)
            return
        player = self._lavalink.player
        if player is None:
            logger.warning('speak_no_player guild_id=%s', self.guild_id)
            await self._drop_head(audio)
            return

        if not audio.track_url:
            logger.info(
                'speak_drop_no_track guild_id=%s message_id=%s content=%r',
                self.guild_id,
                audio.original.id,
                (audio.content or '')[:80],
            )
            await self._drop_head(audio)
            return

        # Lavalink http source: plain URL (bot serves MP3 from RAM).
        try:
            tracks = await wavelink.Pool.fetch_tracks(audio.track_url)
        except wavelink.LavalinkLoadException:
            logger.exception(
                'speak_load_failed guild_id=%s url=%s',
                self.guild_id,
                audio.track_url,
            )
            await self._announcer.announce(
                audio.original,
                'playback',
                'Lavalink could not load the audio.',
            )
            await self._drop_head(audio)
            return
        except wavelink.LavalinkException:
            logger.exception(
                'speak_lavalink_failed guild_id=%s', self.guild_id
            )
            await self._announcer.announce(
                audio.original,
                'playback',
                'Lavalink request failed.',
            )
            await self._drop_head(audio)
            return

        if not tracks or isinstance(tracks, wavelink.Playlist):
            logger.warning(
                'speak_no_tracks guild_id=%s url=%s',
                self.guild_id,
                audio.track_url,
            )
            await self._drop_head(audio)
            return

        track = tracks[0]
        self._current = audio
        self._track_done.clear()
        self._track_failure = None
        length = getattr(track, 'length', None)
        self.current_track_length_ms = (
            int(length) if isinstance(length, int | float) else None
        )
        self.play_started_monotonic = time.monotonic()
        self.last_position_ms = 0
        self.last_progress_monotonic = time.monotonic()
        try:
            with speak_span(
                'play',
                guild_id=self.guild_id,
                message_id=audio.original.id,
            ):
                await self._lavalink.play_track(player, track)
        except wavelink.LavalinkException as exc:
            logger.error(
                'speak_play_failed guild_id=%s lavalink_message=%s',
                self.guild_id,
                getattr(exc, 'message', str(exc)),
            )
            self._current = None
            self._clear_play_progress()
            await self._announcer.announce(
                audio.original,
                'playback',
                'Lavalink rejected play().',
            )
            await self._drop_head(audio)
            return
        except Exception:
            logger.exception('speak_play_failed guild_id=%s', self.guild_id)
            self._current = None
            self._clear_play_progress()
            await self._announcer.announce(
                audio.original,
                'playback',
                'Lavalink rejected play().',
            )
            await self._drop_head(audio)
            return

        with speak_span(
            'await_track_end',
            guild_id=self.guild_id,
            message_id=audio.original.id,
        ):
            await self._track_done.wait()
        self._current = None
        self._clear_play_progress()
        if self._track_failure:
            logger.warning(
                'speak_track_failed guild_id=%s reason=%s',
                self.guild_id,
                self._track_failure,
            )
        else:
            if self._metrics:
                self._metrics.inc('messages_played_total', self.guild_id)
        await self._drop_head(audio)

    async def _drop_head(self, audio: AudioMessage) -> None:
        await self._queue.remove(audio)
        await audio.cleanup()
        if self._metrics:
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
