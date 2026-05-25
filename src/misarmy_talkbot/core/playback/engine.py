"""Per-guild TTS queue worker: drain messages when voice is healthy and announce failures."""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from typing import TYPE_CHECKING, Literal, cast

import discord

from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.playback.queue import MessageQueue
from misarmy_talkbot.core.playback.voice_stop import safe_voice_stop
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.trace import forensic, step

if TYPE_CHECKING:
    import io

    from discord.voice import VoiceClient

    from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
    from misarmy_talkbot.core.playback.audio import AudioMessage
    from misarmy_talkbot.core.voice.pilot import VoicePilot
    from misarmy_talkbot.observability.metrics import MetricsRegistry

PlayResult = Literal['ok', 'client_exception', 'after_error']

# TTS buffers are compressed MP3 from edge/google, not Discord PCM (96_000 B/s).
_DEFAULT_COMPRESSED_BYTES_PER_SECOND = 4_000.0


def _playback_engine_finalize(
    clean: list[bool], object_id: int, guild_id: int
) -> None:
    if not clean[0]:
        logger.warning(
            'LEAK_FINALIZE PlaybackEngine id=%s guild_id=%s',
            object_id,
            guild_id,
        )


class PlaybackEngine:
    """Owns the message queue and FFmpeg playback loop for one guild."""

    def __init__(
        self,
        guild_id: int,
        pilot: VoicePilot,
        announcer: ErrorReplyAnnouncer,
        *,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.guild_id = guild_id
        self._pilot = pilot
        self._announcer = announcer
        self._metrics = metrics
        self._queue = MessageQueue(guild_id)
        self._speak_task: asyncio.Task[None] | None = None
        self._stuck_watch_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._playing: AudioMessage | None = None
        self._speak_loop_iter = 0
        self._last_loop_tick_at: float | None = None
        self._last_step = 'init'
        self._finalize_clean = [False]
        weakref.finalize(
            self,
            _playback_engine_finalize,
            self._finalize_clean,
            id(self),
            guild_id,
        )

    def _max_play_attempts(self) -> int:
        return max(1, int(os.getenv('PLAYBACK_MAX_ATTEMPTS', '2')))

    def _master_voice_channel_id(self) -> int | None:
        master_user_id = FollowRegistry.instance().master(self.guild_id)
        guild = self._pilot._guild()
        if master_user_id is None or guild is None:
            return None
        member = guild.get_member(master_user_id)
        if (
            member is None
            or member.voice is None
            or member.voice.channel is None
        ):
            return None
        return member.voice.channel.id

    def _flow(self, fn: str, phase: str, **fields: object) -> None:
        self._last_step = f'{fn}:{phase}'
        step(
            self.guild_id,
            'engine',
            fn,
            phase,
            loop_iter=self._speak_loop_iter,
            **fields,
        )

    def start(self) -> None:
        if self._speak_task is None or self._speak_task.done():
            self._flow('start', 'ENTER')
            self._speak_task = asyncio.create_task(
                self._supervised_speak(), name=f'speak_{self.guild_id}'
            )
            self._stuck_watch_task = asyncio.create_task(
                self._stuck_watch(), name=f'stuck_{self.guild_id}'
            )
            self._flow('start', 'EXIT', speak_task=repr(self._speak_task))

    async def shutdown(self) -> None:
        try:
            self._flow('shutdown', 'ENTER')
            self._stopped = True
            self._pilot.release_playback_idle('engine_shutdown')
            if self._stuck_watch_task and not self._stuck_watch_task.done():
                self._stuck_watch_task.cancel()
                try:
                    await self._stuck_watch_task
                except asyncio.CancelledError:
                    pass
            if self._speak_task and not self._speak_task.done():
                self._speak_task.cancel()
                try:
                    await self._speak_task
                except asyncio.CancelledError:
                    pass
            await self._queue.clear()
            self._flow('shutdown', 'EXIT')
        finally:
            self._finalize_clean[0] = True

    async def _stuck_watch(self) -> None:
        """Log STUCK when the speak loop stops ticking but the queue still has work."""
        interval = float(os.getenv('TRACE_STUCK_CHECK_SECONDS', '15'))
        threshold = float(os.getenv('TRACE_STUCK_THRESHOLD_SECONDS', '25'))
        while not self._stopped:
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            if self._last_loop_tick_at is None:
                continue
            idle_s = time.monotonic() - self._last_loop_tick_at
            if idle_s < threshold:
                continue
            ready_count = sum(
                1 for item in self._queue if item.state == AudioState.READY
            )
            if ready_count == 0 and len(self._queue) == 0:
                continue
            self._flow(
                'stuck_watch',
                'STUCK',
                idle_s=round(idle_s, 1),
                queue_depth=len(self._queue),
                ready_count=ready_count,
                speak_task_done=self._speak_task.done()
                if self._speak_task
                else None,
                playing_content=repr(self._playing.content)
                if self._playing
                else None,
                pilot_healthy=self._pilot.is_healthy(),
                playback_idle=self._pilot.playback_idle_is_set(),
                gateway_transient=self._pilot.gateway_transient,
            )
            forensic(
                'engine',
                'stuck_snapshot',
                guild_id=self.guild_id,
                queue=repr(self._queue),
            )

    async def enqueue(self, audio: AudioMessage) -> None:
        if self._stopped:
            self._flow(
                'enqueue', 'SKIP', reason='stopped', content=audio.content
            )
            return
        self._flow(
            'enqueue',
            'ENTER',
            content=audio.content,
            queue_depth=len(self._queue),
        )
        await self._queue.put(audio)
        if self._metrics:
            self._metrics.inc('messages_enqueued_total', self.guild_id)
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
        self._flow(
            'enqueue',
            'POINT',
            point='tts_process_start',
            content=audio.content,
        )
        await audio.process()
        self._flow(
            'enqueue',
            'POINT',
            point='tts_process_done',
            content=audio.content,
            state=audio.state.name,
            queue_depth=len(self._queue),
        )
        if audio.state == AudioState.FAILED:
            if self._metrics:
                self._metrics.inc('tts_failures_total', self.guild_id)
            await self._announcer.announce(
                audio.original,
                'tts',
                'The text-to-speech step failed for this message.',
            )
            await self._queue.remove(audio)
            if self._metrics:
                self._metrics.set_gauge(
                    'queue_depth', self.guild_id, float(len(self._queue))
                )
        self._flow(
            'enqueue', 'EXIT', content=audio.content, state=audio.state.name
        )

    async def clear(self) -> None:
        self._flow('clear', 'ENTER', queue_depth=len(self._queue))
        await self._queue.clear()
        if self._metrics:
            self._metrics.set_gauge('queue_depth', self.guild_id, 0.0)
        self._flow('clear', 'EXIT')

    async def on_message_edit(self, message: discord.Message) -> None:
        for queued in self._queue:
            if queued.original == message:
                await queued.edit(message)
                await queued.process()
                break

    async def on_message_delete(self, message: discord.Message) -> None:
        if message in self._queue:
            await self._queue.remove(message)

    async def stop(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'
        member_messages = [
            queued
            for queued in self._queue
            if queued.original.author == member
        ]
        for queued in member_messages:
            await self._queue.remove(queued)
        if (
            self._playing is not None
            and self._playing.original.author == member
        ):
            voice_client = self._pilot.voice_client_ref()
            safe_voice_stop(voice_client, reason='stop_member')
        if self._metrics:
            self._metrics.inc('messages_skipped_total', self.guild_id)
            self._metrics.set_gauge(
                'queue_depth', self.guild_id, float(len(self._queue))
            )
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def stop_all(self) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'
        await self._queue.clear()
        voice_client = self._pilot.voice_client_ref()
        safe_voice_stop(voice_client, reason='stop_all')
        if self._metrics:
            self._metrics.set_gauge('queue_depth', self.guild_id, 0.0)
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def skip(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'
        current = self._playing or self._queue[0]
        if (
            current.original.author != member
            and not member.guild_permissions.mute_members
        ):
            return discord.Colour.red(), 'skip_no_permission'
        voice_client = self._pilot.voice_client_ref()
        if self._playing is not None and voice_client is not None:
            safe_voice_stop(voice_client, reason='skip')
        else:
            await self._queue.remove(current)
        if self._metrics:
            self._metrics.inc('messages_skipped_total', self.guild_id)
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def _supervised_speak(self) -> None:
        self._flow('supervised_speak', 'ENTER')
        backoff = 1.0
        while not self._stopped:
            try:
                await self._speak_loop()
                self._flow(
                    'supervised_speak', 'EXIT', reason='speak_loop_returned'
                )
                return
            except asyncio.CancelledError:
                self._flow('supervised_speak', 'EXIT', reason='cancelled')
                raise
            except Exception:
                logger.exception('speak_task_crash guild_id=%s', self.guild_id)
                self._flow(
                    'supervised_speak', 'POINT', point='crash', backoff=backoff
                )
                if self._metrics:
                    self._metrics.inc(
                        'speak_task_restarts_total', self.guild_id
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _wait_healthy_with_heartbeat(self) -> None:
        """Wait for pilot health without tripping stuck_watch during long gateway waits."""
        if self._pilot.is_healthy():
            return
        wait_task = asyncio.create_task(
            self._pilot.wait_until_healthy(),
            name=f'wait_healthy_{self.guild_id}',
        )
        try:
            while not wait_task.done():
                self._last_loop_tick_at = time.monotonic()
                await asyncio.wait({wait_task}, timeout=5.0)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass
            else:
                await wait_task

    def _play_wait_budget_seconds(
        self, audio_bytes: int, *, text_chars: int = 0
    ) -> float:
        """Upper bound to wait for one clip; derived from compressed TTS size + text length."""
        rate = float(
            os.getenv(
                'PLAYBACK_COMPRESSED_BYTES_PER_SECOND',
                str(_DEFAULT_COMPRESSED_BYTES_PER_SECOND),
            )
        )
        from_bytes = audio_bytes / rate if audio_bytes > 0 else 1.0
        chars_per_sec = float(os.getenv('PLAYBACK_CHARS_PER_SECOND', '14'))
        from_text = text_chars / chars_per_sec if text_chars > 0 else 0.0
        estimated = max(from_bytes, from_text)
        floor_s = float(os.getenv('PLAYBACK_MIN_WAIT_SECONDS', '2'))
        cap_s = float(os.getenv('PLAYBACK_AFTER_TIMEOUT_SECONDS', '30'))
        slack_min = float(os.getenv('PLAYBACK_WAIT_SLACK_SECONDS', '1.0'))
        slack_ratio = float(os.getenv('PLAYBACK_WAIT_SLACK_RATIO', '0.35'))
        slack_s = max(slack_min, estimated * slack_ratio)
        return min(cap_s, max(floor_s, estimated + slack_s))

    async def _wait_play_finished(
        self,
        voice_client: VoiceClient,
        finished: asyncio.Event,
        _play_error: list[BaseException | None],
        *,
        audio_bytes: int,
        text_chars: int = 0,
    ) -> Literal['callback', 'idle', 'timeout_stuck', 'timeout_idle']:
        """Wait for discord ``after`` or for ``is_playing`` to go idle (pycord often omits ``after``)."""
        deadline = time.monotonic() + self._play_wait_budget_seconds(
            audio_bytes, text_chars=text_chars
        )
        poll_s = float(os.getenv('PLAYBACK_IDLE_POLL_SECONDS', '0.05'))
        idle_confirm_s = float(
            os.getenv('PLAYBACK_IDLE_CONFIRM_SECONDS', '0.2')
        )
        saw_playing = False
        idle_since: float | None = None

        while time.monotonic() < deadline:
            if finished.is_set():
                return 'callback'
            if voice_client.is_playing():
                saw_playing = True
                idle_since = None
            elif saw_playing:
                now = time.monotonic()
                if idle_since is None:
                    idle_since = now
                elif now - idle_since >= idle_confirm_s:
                    return 'idle'
            await asyncio.sleep(poll_s)

        if voice_client.is_playing():
            return 'timeout_stuck'
        return 'timeout_idle'

    async def _play_source(
        self,
        voice_client: VoiceClient,
        source: discord.AudioSource,
        *,
        audio_bytes: int,
        text_chars: int = 0,
    ) -> PlayResult:
        self._flow(
            'play_source',
            'ENTER',
            is_playing=voice_client.is_playing(),
            audio_bytes=audio_bytes,
        )
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()
        play_error: list[BaseException | None] = [None]

        def after(error: Exception | None) -> None:
            step(
                self.guild_id,
                'engine',
                'play_after_callback',
                'POINT',
                loop_iter=self._speak_loop_iter,
                error=repr(error),
                is_playing=voice_client.is_playing(),
            )
            if error is not None:
                play_error[0] = error
            loop.call_soon_threadsafe(finished.set)

        self._flow('play_call', 'ENTER', is_playing=voice_client.is_playing())
        try:
            voice_client.play(source, after=after)
        except discord.errors.ClientException as exc:
            logger.warning(
                'speak_play_client_exception guild_id=%s', self.guild_id
            )
            self._flow(
                'play_call', 'EXIT', result='client_exception', error=repr(exc)
            )
            self._flow('play_source', 'EXIT', result='client_exception')
            return 'client_exception'
        self._flow('play_call', 'EXIT', result='invoked')

        wait_budget = self._play_wait_budget_seconds(
            audio_bytes, text_chars=text_chars
        )
        self._flow(
            'play_wait_after',
            'ENTER',
            budget_s=wait_budget,
            audio_bytes=audio_bytes,
        )
        wait_result = await self._wait_play_finished(
            voice_client,
            finished,
            play_error,
            audio_bytes=audio_bytes,
            text_chars=text_chars,
        )

        if wait_result == 'callback':
            if play_error[0] is not None:
                logger.warning(
                    'speak_play_after_error guild_id=%s err=%r',
                    self.guild_id,
                    play_error[0],
                )
                self._flow(
                    'play_wait_after',
                    'EXIT',
                    result='after_error',
                    error=repr(play_error[0]),
                )
                self._flow('play_source', 'EXIT', result='after_error')
                return 'after_error'
            self._flow('play_wait_after', 'EXIT', result='callback_ok')
            self._flow('play_source', 'EXIT', result='ok')
            return 'ok'

        if wait_result == 'idle':
            logger.debug(
                'speak_play_idle_done guild_id=%s audio_bytes=%s (after callback missing)',
                self.guild_id,
                audio_bytes,
            )
            self._flow('play_wait_after', 'EXIT', result='idle_ok')
            self._flow('play_source', 'EXIT', result='ok')
            return 'ok'

        if wait_result == 'timeout_idle':
            logger.warning(
                'speak_play_after_missing guild_id=%s audio_bytes=%s; voice idle, advancing queue',
                self.guild_id,
                audio_bytes,
            )
            self._flow('play_wait_after', 'EXIT', result='timeout_idle_ok')
            self._flow('play_source', 'EXIT', result='ok')
            return 'ok'

        logger.warning(
            'speak_play_stuck guild_id=%s audio_bytes=%s is_playing=%s; advancing (is_playing unreliable)',
            self.guild_id,
            audio_bytes,
            voice_client.is_playing(),
        )
        safe_voice_stop(voice_client, reason='play_timeout_stuck')
        self._flow('play_wait_after', 'EXIT', result='timeout_stuck_ok')
        self._flow('play_source', 'EXIT', result='ok')
        return 'ok'

    async def _speak_loop(self) -> None:
        self._flow('speak_loop', 'ENTER')
        while not self._stopped:
            self._speak_loop_iter += 1
            self._last_loop_tick_at = time.monotonic()
            self._flow(
                'speak_loop',
                'LOOP',
                queue_depth=len(self._queue),
                playing=repr(self._playing.content) if self._playing else None,
            )
            forensic(
                'engine',
                'loop_snapshot',
                guild_id=self.guild_id,
                queue=repr(self._queue),
                pilot_healthy=self._pilot.is_healthy(),
                playback_idle=self._pilot.playback_idle_is_set(),
            )

            self._flow('wait_head', 'ENTER', want_state='READY')
            await self._queue.wait_until_head(AudioState.READY)
            head = self._queue[0]
            self._flow(
                'wait_head',
                'EXIT',
                head_content=head.content,
                head_state=head.state.name,
                queue_depth=len(self._queue),
            )

            head_bytes = (
                head.buffer.getbuffer().nbytes
                if head.buffer is not None
                else 0
            )
            if head.buffer is None or head_bytes == 0:
                self._flow(
                    'skip_no_buffer',
                    'SKIP',
                    content=head.content,
                    head_bytes=head_bytes,
                )
                await self._queue.get(AudioState.READY, index=0)
                if self._metrics:
                    self._metrics.set_gauge(
                        'queue_depth', self.guild_id, float(len(self._queue))
                    )
                self._flow('speak_loop', 'LOOP_END', outcome='skip_no_buffer')
                continue

            self._flow('align_master', 'ENTER')
            await self._pilot.align_to_master_channel()
            self._flow('align_master', 'EXIT')

            voice_client = self._pilot.voice_client_ref()
            bot_channel_id = (
                voice_client.channel.id
                if voice_client and voice_client.channel
                else None
            )
            master_channel_id = self._master_voice_channel_id()
            self._flow(
                'voice_check',
                'POINT',
                connected=voice_client.is_connected()
                if voice_client
                else False,
                bot_channel_id=bot_channel_id,
                master_channel_id=master_channel_id,
                channel_match=(
                    bot_channel_id == master_channel_id
                    if bot_channel_id and master_channel_id
                    else None
                ),
            )
            if voice_client is None or not voice_client.is_connected():
                logger.warning(
                    'speak_voice_down guild_id=%s queue_depth=%s',
                    self.guild_id,
                    len(self._queue),
                )
                self._flow('recover', 'ENTER')
                await self._pilot.recover(urgent=True)
                self._flow('recover', 'EXIT')
                voice_client = self._pilot.voice_client_ref()

            self._flow(
                'wait_healthy', 'ENTER', pilot_healthy=self._pilot.is_healthy()
            )
            await self._wait_healthy_with_heartbeat()
            self._flow(
                'wait_healthy', 'EXIT', pilot_healthy=self._pilot.is_healthy()
            )

            voice_client = self._pilot.voice_client_ref()
            if voice_client is None or not voice_client.is_connected():
                self._flow('voice_still_down', 'POINT', action='backoff_1s')
                await asyncio.sleep(1.0)
                self._flow(
                    'speak_loop', 'LOOP_END', outcome='voice_still_down'
                )
                continue

            self._flow('dequeue', 'ENTER', head_content=head.content)
            msg = await self._queue.get(AudioState.READY, index=0)
            self._playing = msg
            self._flow(
                'dequeue',
                'EXIT',
                content=msg.content,
                queue_depth=len(self._queue),
            )

            play_result: PlayResult = 'after_error'
            play_channel_id = (
                voice_client.channel.id if voice_client.channel else None
            )
            msg_bytes = (
                msg.buffer.getbuffer().nbytes if msg.buffer is not None else 0
            )
            try:
                logger.debug(
                    'speak_play_start guild_id=%s channel_id=%s content=%r bytes=%s',
                    self.guild_id,
                    play_channel_id,
                    msg.content,
                    msg_bytes,
                )
                cast('io.BytesIO', msg.buffer).seek(0)
                source = discord.FFmpegPCMAudio(
                    cast('io.BytesIO', msg.buffer), pipe=True
                )
                self._flow('playback_active', 'ENTER', content=msg.content)
                async with self._pilot.playback_active():
                    play_result = await self._play_source(
                        voice_client,
                        source,
                        audio_bytes=msg_bytes,
                        text_chars=len(msg.content),
                    )
                self._flow(
                    'playback_active',
                    'EXIT',
                    content=msg.content,
                    play_result=play_result,
                )
            except Exception:
                logger.exception(
                    'speak_play_unhandled guild_id=%s content=%r',
                    self.guild_id,
                    msg.content,
                )
                self._pilot.release_playback_idle('speak_play_unhandled')
                play_result = 'after_error'
            finally:
                self._playing = None
                self._flow('playing_cleared', 'POINT', content=msg.content)

            if play_result != 'ok':
                msg.play_attempts += 1
                max_attempts = self._max_play_attempts()
                if msg.play_attempts >= max_attempts:
                    logger.warning(
                        'speak_drop_max_attempts guild_id=%s content=%r attempts=%s play_result=%s',
                        self.guild_id,
                        msg.content,
                        msg.play_attempts,
                        play_result,
                    )
                    self._flow(
                        'speak_drop',
                        'SKIP',
                        content=msg.content,
                        play_result=play_result,
                        attempts=msg.play_attempts,
                    )
                    self._flow(
                        'speak_loop',
                        'LOOP_END',
                        outcome='dropped',
                        content=msg.content,
                    )
                    continue

                self._flow(
                    'speak_retry',
                    'RETRY',
                    content=msg.content,
                    play_result=play_result,
                    attempt=msg.play_attempts,
                    max_attempts=max_attempts,
                )
                await self._queue.insert_head(msg)
                if play_result == 'client_exception':
                    self._flow(
                        'force_fresh', 'ENTER', reason='play_client_exception'
                    )
                    await self._pilot._force_fresh_connect(
                        'play_client_exception'
                    )
                    self._flow(
                        'force_fresh', 'EXIT', reason='play_client_exception'
                    )
                else:
                    await asyncio.sleep(0.3)
                self._flow(
                    'speak_loop',
                    'LOOP_END',
                    outcome='retry',
                    play_result=play_result,
                )
                continue

            logger.debug(
                'speak_play_done guild_id=%s channel_id=%s content=%r',
                self.guild_id,
                play_channel_id,
                msg.content,
            )
            if self._metrics:
                self._metrics.inc('messages_played_total', self.guild_id)
                self._metrics.set_gauge(
                    'queue_depth', self.guild_id, float(len(self._queue))
                )
            self._flow(
                'speak_loop',
                'LOOP_END',
                outcome='played_ok',
                content=msg.content,
            )

        self._flow('speak_loop', 'EXIT', reason='stopped')
