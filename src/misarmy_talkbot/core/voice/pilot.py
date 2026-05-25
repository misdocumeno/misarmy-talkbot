"""Guild-scoped voice connection owner (serialize connects, moves, and teardown).

The pilot exists because discord.py voice state is easy to race: multiple coroutines
can otherwise fight over ``VoiceClient`` instances. One asyncio lock plus a supervisor
task gives playback and slash commands a single lane for recovery and close-code handling.
"""

from __future__ import annotations

import asyncio
import os
import time
import weakref
from collections import deque
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, cast

import discord

from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.voice_stop import safe_voice_stop
from misarmy_talkbot.core.voice.close_codes import (
    library_grace_s,
    rate_limit_backoff_s,
    unhealthy_grace,
    voice_connect_timeout,
    wait_4014_s,
    watchdog_interval,
)
from misarmy_talkbot.core.voice.voice_client import MisarmyVoiceClient
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.trace import forensic, step

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        Awaitable,
        Callable,
    )

    from discord.voice import VoiceClient

    from misarmy_talkbot.observability.metrics import MetricsRegistry


def _voice_pilot_finalize(
    clean: list[bool], object_id: int, guild_id: int
) -> None:
    if not clean[0]:
        logger.warning(
            'LEAK_FINALIZE VoicePilot id=%s guild_id=%s', object_id, guild_id
        )


def _as_voice_connect_channel(
    channel: discord.abc.GuildChannel | discord.abc.PrivateChannel | None,
) -> discord.VoiceChannel | discord.StageChannel | None:
    """Return channel if the bot can call ``.connect()`` on it.

    ``discord.abc.Connectable`` is not ``@runtime_checkable``, so ``isinstance``
    against it raises on Python 3.12+.
    """
    if isinstance(channel, discord.VoiceChannel | discord.StageChannel):
        return channel
    return None


class VoicePilot:
    """Serialize voice connect/move/disconnect and interpret gateway close codes for one guild."""

    def __init__(
        self,
        bot: discord.Bot,
        guild_id: int,
        *,
        metrics: MetricsRegistry | None = None,
        on_dave_required: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bot = bot
        self.guild_id = guild_id
        self._metrics = metrics
        self._on_dave_required = on_dave_required
        self._lock = asyncio.Lock()
        self.intended_channel_id: int | None = None
        self._voice_client: VoiceClient | None = None
        self._close_queue: asyncio.Queue[int] = asyncio.Queue()
        self._healthy_event = asyncio.Event()
        self._healthy_event.set()
        self._unhealthy_since: float | None = None
        self.gateway_transient = False
        self._gateway_transient_since: float | None = None
        self._recover_debounce_task: asyncio.Task[None] | None = None
        self._recover_not_before: float = 0.0
        self._last_teardown_at: float | None = None
        self._last_teardown_reason: str | None = None
        self._stopped = False
        self._fatal_voice = False
        self._server_signal = asyncio.Event()
        self.recover_count = 0
        self.last_recover_at: float | None = None
        self._playback_idle = asyncio.Event()
        self._playback_idle.set()
        self.close_code_history: deque[tuple[float, int]] = deque(maxlen=16)
        self._supervisor_task: asyncio.Task[None] | None = None
        self._finalize_clean = [False]
        weakref.finalize(
            self,
            _voice_pilot_finalize,
            self._finalize_clean,
            id(self),
            guild_id,
        )

    def _guild(self) -> discord.Guild | None:
        return self._bot.get_guild(self.guild_id)

    def voice_client_ref(self) -> VoiceClient | None:
        return self._voice_client

    def last_teardown_context(self) -> dict[str, object]:
        """Recent intentional teardown (for correlating gateway voice_state drops)."""
        if self._last_teardown_at is None:
            return {'reason': None, 'age_s': None}
        return {
            'reason': self._last_teardown_reason,
            'age_s': round(time.monotonic() - self._last_teardown_at, 2),
        }

    def playback_idle_is_set(self) -> bool:
        return self._playback_idle.is_set()

    def release_playback_idle(self, reason: str) -> None:
        """Unblock voice ops if ``after`` never fired (avoids permanent follow-deafness)."""
        if not self._playback_idle.is_set():
            logger.warning(
                'playback_idle_force_release guild_id=%s reason=%s',
                self.guild_id,
                reason,
            )
            self._playback_idle.set()

    async def _wait_playback_idle(self, *, reason: str) -> None:
        """Block voice reconnect/move until FFmpeg playback has finished."""
        timeout = float(os.getenv('PLAYBACK_IDLE_WAIT_SECONDS', '30'))
        step(
            self.guild_id,
            'pilot',
            'wait_playback_idle',
            'ENTER',
            reason=reason,
            idle_set=self._playback_idle.is_set(),
            timeout_s=timeout,
        )
        try:
            await asyncio.wait_for(self._playback_idle.wait(), timeout=timeout)
            step(
                self.guild_id,
                'pilot',
                'wait_playback_idle',
                'EXIT',
                reason=reason,
            )
        except TimeoutError:
            step(
                self.guild_id,
                'pilot',
                'wait_playback_idle',
                'EXIT',
                reason=reason,
                result='timeout',
            )
            self.release_playback_idle('playback_idle_wait_timeout')
            safe_voice_stop(
                self._voice_client, reason='playback_idle_wait_timeout'
            )

    @asynccontextmanager
    async def playback_active(self) -> AsyncGenerator[None, None]:
        """Mark active playback so connection teardown cannot race ``VoiceClient.play``."""
        step(self.guild_id, 'pilot', 'playback_idle_acquire', 'ENTER')
        await self._playback_idle.wait()
        self._playback_idle.clear()
        step(
            self.guild_id,
            'pilot',
            'playback_idle_acquire',
            'EXIT',
            idle_set=False,
        )
        try:
            yield
        finally:
            self._playback_idle.set()
            step(
                self.guild_id,
                'pilot',
                'playback_idle_release',
                'EXIT',
                idle_set=True,
            )

    def _sync_healthy_event(self) -> None:
        if self.is_healthy():
            self._healthy_event.set()
        else:
            self._healthy_event.clear()

    def set_gateway_transient(self, transient: bool) -> None:
        """Mark short gateway outages so playback pauses without treating them as voice bugs."""
        step(
            self.guild_id,
            'pilot',
            'gateway_transient',
            'POINT',
            transient=transient,
            healthy_after=not transient and not self._fatal_voice,
        )
        self.gateway_transient = transient
        self._gateway_transient_since = time.monotonic() if transient else None
        self._sync_healthy_event()

    def _voice_linked(self) -> bool:
        """True when the bot is in the intended voice channel."""
        if self.intended_channel_id is None:
            return False
        guild = self._guild()
        voice_client = self._voice_client or (
            guild.voice_client if guild else None
        )
        channel = (
            None
            if voice_client is None
            else getattr(voice_client, 'channel', None)
        )
        if (
            voice_client is None
            or not voice_client.is_connected()
            or channel is None
        ):
            return False
        return channel.id == self.intended_channel_id

    def _clear_stale_gateway_transient(self) -> None:
        """Drop a gateway-transient flag that outlived the actual outage (common after long idle)."""
        if not self.gateway_transient:
            return
        logger.debug(
            'gateway_transient_cleared guild_id=%s voice_linked=%s',
            self.guild_id,
            self._voice_linked(),
        )
        self.set_gateway_transient(False)

    def _voice_channel_id(
        self, voice_client: VoiceClient | None
    ) -> int | None:
        if voice_client is None:
            return None
        channel = getattr(voice_client, 'channel', None)
        if channel is None:
            return None
        return channel.id

    def _voice_ws_close_code(
        self, voice_client: VoiceClient | None
    ) -> int | None:
        if voice_client is None:
            return None
        try:
            ws = getattr(voice_client, 'ws', None)
            if ws is None:
                return None
            code = getattr(ws, 'close_code', None)
            return int(code) if code is not None else None
        except (TypeError, ValueError):
            return None

    async def _disconnect_voice_client(
        self,
        voice_client: VoiceClient | None,
        *,
        reason: str,
    ) -> None:
        """Disconnect while optionally suppressing close-code forwarding (intentional teardown)."""
        if voice_client is None:
            return
        channel_id = self._voice_channel_id(voice_client)
        ws_code = self._voice_ws_close_code(voice_client)
        self._last_teardown_at = time.monotonic()
        self._last_teardown_reason = reason
        logger.info(
            'voice_teardown_initiated guild_id=%s reason=%s channel_id=%s ws_close_code=%s initiated_by=us',
            self.guild_id,
            reason,
            channel_id,
            ws_code,
        )
        use_suppress = isinstance(voice_client, MisarmyVoiceClient)
        if use_suppress:
            voice_client.set_suppress_close_observer(True)
        safe_voice_stop(voice_client, reason=f'teardown:{reason}')
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            logger.exception(
                'voice_teardown_disconnect_failed guild_id=%s reason=%s',
                self.guild_id,
                reason,
            )
        finally:
            if use_suppress:
                voice_client.set_suppress_close_observer(False)

    def _log_voice_connect(self, *, reason: str, channel_id: int) -> None:
        logger.info(
            'voice_connect_initiated guild_id=%s reason=%s channel_id=%s initiated_by=us',
            self.guild_id,
            reason,
            channel_id,
        )

    def is_healthy(self) -> bool:
        if self._fatal_voice:
            return False
        if self.gateway_transient:
            max_age = float(os.getenv('GATEWAY_TRANSIENT_MAX_SECONDS', '60'))
            age_s = (
                time.monotonic() - self._gateway_transient_since
                if self._gateway_transient_since is not None
                else max_age
            )
            if self._voice_linked() or age_s >= max_age:
                self._clear_stale_gateway_transient()
            else:
                return False
        if self.intended_channel_id is None:
            return True
        if not self._voice_linked():
            return False
        return self._unhealthy_since is None

    async def wait_until_healthy(self) -> None:
        """Block callers (playback) until gateway and voice state allow sending audio again."""
        if self.is_healthy():
            step(
                self.guild_id,
                'pilot',
                'wait_healthy',
                'EXIT',
                result='fast_path',
            )
            return
        timeout = float(os.getenv('HEALTHY_WAIT_TIMEOUT_SECONDS', '90'))
        step(
            self.guild_id,
            'pilot',
            'wait_healthy',
            'ENTER',
            gateway_transient=self.gateway_transient,
            fatal_voice=self._fatal_voice,
            timeout_s=timeout,
        )
        forensic(
            'pilot',
            'wait_healthy_snapshot',
            guild_id=self.guild_id,
            intended_channel_id=self.intended_channel_id,
            voice_connected=(
                self._voice_client.is_connected()
                if self._voice_client
                else False
            ),
            playback_idle=self._playback_idle.is_set(),
        )
        try:
            await asyncio.wait_for(self._healthy_event.wait(), timeout=timeout)
            step(
                self.guild_id,
                'pilot',
                'wait_healthy',
                'EXIT',
                healthy=self.is_healthy(),
            )
        except TimeoutError:
            step(
                self.guild_id,
                'pilot',
                'wait_healthy',
                'EXIT',
                result='timeout',
                gateway_transient=self.gateway_transient,
            )
            voice_client = self._voice_client
            connected = (
                voice_client is not None and voice_client.is_connected()
            )
            logger.warning(
                'wait_until_healthy_timeout guild_id=%s gateway_transient=%s voice_connected=%s',
                self.guild_id,
                self.gateway_transient,
                connected,
            )
            if self.gateway_transient and connected:
                self.set_gateway_transient(False)
            elif connected:
                self._mark_healthy()

    def _mark_unhealthy(self) -> None:
        if self._unhealthy_since is None:
            self._unhealthy_since = time.monotonic()
        self._sync_healthy_event()

    def _mark_healthy(self) -> None:
        self._unhealthy_since = None
        self._sync_healthy_event()

    def start_supervisor(self) -> None:
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(
                self._supervised_recovery(),
                name=f'pilot_super_{self.guild_id}',
            )

    async def stop_supervisor(self) -> None:
        self._stopped = True
        if self._supervisor_task and not self._supervisor_task.done():
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass

    async def _supervised_recovery(self) -> None:
        backoff = 1.0
        while not self._stopped:
            try:
                await self._recovery_supervisor()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    'recovery_supervisor_crash guild_id=%s', self.guild_id
                )
                if self._metrics:
                    self._metrics.inc(
                        'recovery_supervisor_restarts_total', self.guild_id
                    )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 30.0)

    async def _recovery_supervisor(self) -> None:
        watchdog = watchdog_interval()
        while not self._stopped:
            try:
                code = await asyncio.wait_for(
                    self._close_queue.get(), timeout=watchdog
                )
            except TimeoutError:
                code = None

            if code is not None:
                self.close_code_history.append((time.monotonic(), code))
                await self._handle_close_code(code)
                continue

            if self.intended_channel_id is None:
                continue
            voice_client = self._voice_client
            if voice_client is None or not voice_client.is_connected():
                now = time.monotonic()
                if self._unhealthy_since is None:
                    self._unhealthy_since = now
                elif now - self._unhealthy_since >= unhealthy_grace():
                    step(
                        self.guild_id,
                        'pilot',
                        'watchdog',
                        'POINT',
                        action='force_fresh',
                    )
                    await self._force_fresh_connect('watchdog_unhealthy')
                    self._mark_healthy()
            else:
                if self._unhealthy_since is not None:
                    self._mark_healthy()

    async def _handle_close_code(self, code: int) -> None:
        logger.warning(
            'voice_close_code_received guild_id=%s code=%s initiated_by=voice_ws '
            '(close observer; not a suppressed pilot teardown)',
            self.guild_id,
            code,
        )
        step(self.guild_id, 'pilot', 'handle_close_code', 'ENTER', code=code)
        if self._metrics:
            key = (
                f'pilot_close_{code}_total'
                if code in (4006, 4009, 4014, 4015, 4022)
                else 'pilot_close_other_total'
            )
            self._metrics.inc(key, self.guild_id)

        if code == 1000:
            step(
                self.guild_id,
                'pilot',
                'handle_close_code',
                'EXIT',
                code=code,
                action='noop_1000',
            )
            return
        if code == 4017:
            self._fatal_voice = True
            self._mark_unhealthy()
            if self._on_dave_required:
                await self._on_dave_required()
            return

        if code in (4006, 4009, 4011, 4016):
            await self._force_fresh_connect(f'close_{code}')
            return

        if code == 4014:
            self._server_signal.clear()
            try:
                await asyncio.wait_for(
                    self._server_signal.wait(), timeout=wait_4014_s()
                )
            except TimeoutError:
                logger.info('4014_treated_as_kick guild_id=%s', self.guild_id)
            finally:
                self._server_signal.clear()
            await self._force_fresh_connect('close_4014')
            return

        if code == 4015:
            await asyncio.sleep(library_grace_s())
            if not self.is_healthy():
                await self._force_fresh_connect('close_4015_backstop')
            return

        if code == 4021 or code == 4022:
            await asyncio.sleep(rate_limit_backoff_s())
            await self._force_fresh_connect(f'close_{code}')
            return

        await asyncio.sleep(library_grace_s())
        if not self.is_healthy():
            await self._force_fresh_connect('close_generic_backstop')

    async def _force_fresh_connect(self, reason: str) -> None:
        step(self.guild_id, 'pilot', 'force_fresh', 'ENTER', reason=reason)
        await self._wait_playback_idle(reason=reason)
        async with self._lock:
            await self._force_fresh_connect_locked(reason)
        step(self.guild_id, 'pilot', 'force_fresh', 'EXIT', reason=reason)

    async def _force_fresh_connect_locked(self, reason: str) -> None:
        step(
            self.guild_id,
            'pilot',
            'force_fresh_locked',
            'ENTER',
            reason=reason,
        )
        guild = self._guild()
        if guild is None or self.intended_channel_id is None:
            return
        channel = _as_voice_connect_channel(
            guild.get_channel(self.intended_channel_id)
        )
        if channel is None:
            logger.warning(
                'force_fresh_connect_no_channel guild_id=%s reason=%s',
                self.guild_id,
                reason,
            )
            return

        previous_client = self._voice_client
        if previous_client is not None:
            await self._disconnect_voice_client(
                previous_client, reason=f'force_fresh:{reason}'
            )
            self._voice_client = None

        self._log_voice_connect(
            reason=f'force_fresh:{reason}', channel_id=channel.id
        )
        try:
            connected_client = await channel.connect(
                cls=cast('type[VoiceClient]', MisarmyVoiceClient),
                reconnect=True,
                timeout=voice_connect_timeout(),
            )
            assert isinstance(connected_client, MisarmyVoiceClient)
            connected_client.attach_observer(self._close_queue)
            connected_client.attach_server_signal(self._server_signal)
            self._voice_client = connected_client
            await guild.change_voice_state(channel=channel, self_deaf=True)
        except Exception:
            logger.exception(
                'force_fresh_connect_failed guild_id=%s reason=%s',
                self.guild_id,
                reason,
            )
            self._mark_unhealthy()
            return

        logger.info(
            'voice_connect_completed guild_id=%s reason=force_fresh:%s channel_id=%s',
            self.guild_id,
            reason,
            channel.id,
        )
        self.recover_count += 1
        self.last_recover_at = time.monotonic()
        if self._metrics:
            self._metrics.inc('pilot_recovers_total', self.guild_id)
        logger.info(
            'pilot_recover guild_id=%s reason=%s count=%s',
            self.guild_id,
            reason,
            self.recover_count,
        )
        self._clear_stale_gateway_transient()
        self._mark_healthy()
        step(
            self.guild_id, 'pilot', 'force_fresh_locked', 'EXIT', reason=reason
        )

    async def refresh_after_peer_drop(self) -> None:
        """Rebuild voice when another followed user leaves (waits for any active play first)."""
        step(self.guild_id, 'pilot', 'refresh_peer_drop', 'ENTER')
        if self.intended_channel_id is None:
            step(
                self.guild_id,
                'pilot',
                'refresh_peer_drop',
                'EXIT',
                result='skip_no_intended',
            )
            return
        logger.info('voice_refresh_peer_drop guild_id=%s', self.guild_id)
        await self._force_fresh_connect('peer_grace_drop')
        step(self.guild_id, 'pilot', 'refresh_peer_drop', 'EXIT')

    async def ensure_connected_to(
        self, channel_id: int
    ) -> None | PermissionError:
        """Set intended channel and connect or reuse an existing compatible voice client."""
        step(
            self.guild_id,
            'pilot',
            'ensure_connected',
            'ENTER',
            channel_id=channel_id,
        )
        await self._wait_playback_idle(reason='ensure_connected_to')
        async with self._lock:
            guild = self._guild()
            if guild is None:
                return PermissionError('guild missing')
            channel = _as_voice_connect_channel(guild.get_channel(channel_id))
            if channel is None:
                return PermissionError('channel missing')
            perms = channel.permissions_for(guild.me)
            if not perms.connect or not perms.speak:
                return PermissionError('missing voice perms')

            self.intended_channel_id = channel_id
            voice_client = guild.voice_client
            if (
                voice_client is not None
                and voice_client.is_connected()
                and voice_client.channel
                and voice_client.channel.id == channel_id
            ):
                if isinstance(voice_client, MisarmyVoiceClient):
                    voice_client.attach_observer(self._close_queue)
                    voice_client.attach_server_signal(self._server_signal)
                self._voice_client = voice_client
                await guild.change_voice_state(channel=channel, self_deaf=True)
                self._clear_stale_gateway_transient()
                self._mark_healthy()
                return None

            if voice_client is not None:
                await self._disconnect_voice_client(
                    voice_client, reason='ensure_connected_replace'
                )
                self._voice_client = None

            self._log_voice_connect(
                reason='ensure_connected', channel_id=channel.id
            )
            try:
                connected_client = await channel.connect(
                    cls=cast('type[VoiceClient]', MisarmyVoiceClient),
                    reconnect=True,
                    timeout=voice_connect_timeout(),
                )
                assert isinstance(connected_client, MisarmyVoiceClient)
                connected_client.attach_observer(self._close_queue)
                connected_client.attach_server_signal(self._server_signal)
                self._voice_client = connected_client
                await guild.change_voice_state(channel=channel, self_deaf=True)
            except discord.errors.Forbidden:
                return PermissionError('forbidden connect')
            except Exception:
                logger.exception(
                    'ensure_connected_failed guild_id=%s channel_id=%s',
                    self.guild_id,
                    channel_id,
                )
                self._mark_unhealthy()
                return PermissionError('connect failed')

            logger.info(
                'voice_connect_completed guild_id=%s reason=ensure_connected channel_id=%s',
                self.guild_id,
                channel_id,
            )
            self._clear_stale_gateway_transient()
            self._mark_healthy()
            return None

    async def move(self, channel_id: int) -> None | PermissionError:
        step(self.guild_id, 'pilot', 'move', 'ENTER', channel_id=channel_id)
        await self._wait_playback_idle(reason='move')
        async with self._lock:
            guild = self._guild()
            if guild is None:
                return PermissionError('guild missing')
            channel = _as_voice_connect_channel(guild.get_channel(channel_id))
            if channel is None:
                return PermissionError('channel missing')
            perms = channel.permissions_for(guild.me)
            if not perms.connect or not perms.speak:
                return PermissionError('missing voice perms')
            self.intended_channel_id = channel_id
            voice_client = self._voice_client or guild.voice_client
            if voice_client is None or not voice_client.is_connected():
                await self._force_fresh_connect_locked('move_without_vc')
                return None
            try:
                await voice_client.move_to(channel)
                await guild.change_voice_state(channel=channel, self_deaf=True)
            except discord.errors.Forbidden:
                return PermissionError('forbidden move')
            self._voice_client = voice_client
            self._mark_healthy()
            return None

    async def disconnect(self) -> None:
        try:
            await self._wait_playback_idle(reason='pilot_disconnect')
            async with self._lock:
                self.intended_channel_id = None
                guild = self._guild()
                voice_client = self._voice_client or (
                    guild.voice_client if guild else None
                )
                self._voice_client = None
                await self._disconnect_voice_client(
                    voice_client, reason='pilot_disconnect'
                )
                self._mark_healthy()
        finally:
            self._finalize_clean[0] = True

    async def align_to_master_channel(self) -> bool:
        """Connect or move into the follow master's current voice channel when drifted."""
        step(self.guild_id, 'pilot', 'align_to_master', 'ENTER')
        master_user_id = FollowRegistry.instance().master(self.guild_id)
        guild = self._guild()
        if guild is None or master_user_id is None:
            step(
                self.guild_id,
                'pilot',
                'align_to_master',
                'EXIT',
                result='no_guild_or_master',
            )
            return False
        master_member = guild.get_member(master_user_id)
        if (
            master_member is None
            or master_member.voice is None
            or master_member.voice.channel is None
        ):
            step(
                self.guild_id,
                'pilot',
                'align_to_master',
                'EXIT',
                result='master_not_in_vc',
            )
            return False
        target_id = master_member.voice.channel.id
        voice_client = self._voice_client or guild.voice_client
        current_id = (
            voice_client.channel.id
            if voice_client is not None and voice_client.channel
            else None
        )
        if (
            voice_client is not None
            and voice_client.is_connected()
            and current_id == target_id
        ):
            self.intended_channel_id = target_id
            step(
                self.guild_id,
                'pilot',
                'align_to_master',
                'EXIT',
                result='already_aligned',
                channel_id=target_id,
            )
            return False
        logger.info(
            'align_to_master guild_id=%s from_channel=%s to_channel=%s',
            self.guild_id,
            current_id,
            target_id,
        )
        error = await self.ensure_connected_to(target_id)
        step(
            self.guild_id,
            'pilot',
            'align_to_master',
            'EXIT',
            result='reconnected' if error is None else 'error',
            from_channel=current_id,
            to_channel=target_id,
            error=repr(error) if error else None,
        )
        return error is None

    def schedule_recover(self) -> None:
        """Debounce involuntary disconnect recovery (avoids reconnect storms)."""
        if (
            self._recover_debounce_task is not None
            and not self._recover_debounce_task.done()
        ):
            self._recover_debounce_task.cancel()
        self._recover_debounce_task = asyncio.create_task(
            self._debounced_recover(),
            name=f'recover_debounce_{self.guild_id}',
        )

    async def _debounced_recover(self) -> None:
        debounce_s = float(os.getenv('VOICE_RECOVER_DEBOUNCE_SECONDS', '3'))
        try:
            await asyncio.sleep(debounce_s)
        except asyncio.CancelledError:
            raise
        if self._voice_linked():
            step(
                self.guild_id,
                'pilot',
                'recover',
                'SKIP',
                reason='already_connected_after_debounce',
            )
            self._clear_stale_gateway_transient()
            self._mark_healthy()
            return
        await self.recover()

    async def recover(self, *, urgent: bool = False) -> None:
        """Reconnect to wherever the follow master is after an involuntary disconnect.

        Invoked when the bot's own voice state shows ``channel is None`` but the registry
        still lists a master: we try to catch up without mutating follow membership.
        """
        if not urgent:
            now = time.monotonic()
            if now < self._recover_not_before:
                step(
                    self.guild_id,
                    'pilot',
                    'recover',
                    'SKIP',
                    reason='cooldown',
                    not_before_in_s=round(self._recover_not_before - now, 1),
                )
                return

        step(self.guild_id, 'pilot', 'recover', 'ENTER')
        master_user_id = FollowRegistry.instance().master(self.guild_id)
        guild = self._guild()
        if guild is None or master_user_id is None:
            logger.info('bot_isolated guild_id=%s (no master)', self.guild_id)
            return
        master_member = guild.get_member(master_user_id)
        if (
            master_member is None
            or master_member.voice is None
            or master_member.voice.channel is None
        ):
            logger.info(
                'bot_isolated guild_id=%s (master not in vc)', self.guild_id
            )
            return
        error = await self.ensure_connected_to(master_member.voice.channel.id)
        if error is not None:
            logger.info(
                'recover_skipped guild_id=%s err=%s', self.guild_id, error
            )
        else:
            self._clear_stale_gateway_transient()
            cooldown_s = float(
                os.getenv('VOICE_RECOVER_COOLDOWN_SECONDS', '20')
            )
            self._recover_not_before = time.monotonic() + cooldown_s
        step(
            self.guild_id,
            'pilot',
            'recover',
            'EXIT',
            error=repr(error) if error else None,
        )
