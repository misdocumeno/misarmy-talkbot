"""Per-guild ordered queue of ``AudioMessage`` items.

The engine waits on a condition variable for the head to reach a target state
(typically ``READY``) without polling. Items can be inserted at the head when a
play call needs a retry, and removed by message identity for ``on_message_delete``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.audio import AudioMessage

if TYPE_CHECKING:
    from collections.abc import Iterator

    import discord

    from misarmy_talkbot.core.playback.audio import AudioState


class MessageQueue:
    def __init__(self, guild_id: int) -> None:
        self._guild_id = guild_id
        self.__items: list[AudioMessage] = []
        self.__condition = asyncio.Condition()

    async def put(self, item: AudioMessage) -> None:
        self.__items.append(item)
        await self._notify()

    async def insert_head(self, item: AudioMessage) -> None:
        self.__items.insert(0, item)
        await self._notify()

    async def remove(self, item: AudioMessage | discord.Message) -> None:
        if isinstance(item, AudioMessage):
            if item in self.__items:
                self.__items.remove(item)
        else:
            for message in list(self.__items):
                if message.original == item:
                    self.__items.remove(message)
                    break
        await self._notify()

    async def clear(self) -> None:
        self.__items.clear()
        await self._notify()

    async def wait_until_head(self, state: AudioState) -> AudioMessage:
        """Block until ``__items[0]`` is in ``state`` (does not remove it)."""
        async with self.__condition:
            while len(self.__items) == 0 or self.__items[0].state != state:
                await self.__condition.wait()
            return self.__items[0]

    async def notify_state_change(self) -> None:
        """Wake any ``wait_until_head`` callers; called by the engine after process()."""
        await self._notify()

    async def _notify(self) -> None:
        async with self.__condition:
            self.__condition.notify_all()

    def __getitem__(self, index: int) -> AudioMessage:
        return self.__items[index]

    def __setitem__(self, index: int, message: AudioMessage) -> None:
        self.__items[index] = message

    def __contains__(self, item: object) -> bool:
        return item in self.__items

    def __iter__(self) -> Iterator[AudioMessage]:
        return iter(self.__items)

    def __len__(self) -> int:
        return len(self.__items)

    def head(self) -> AudioMessage | None:
        return self.__items[0] if self.__items else None

    def __repr__(self) -> str:
        return f'[{", ".join([repr(x) for x in self.__items])}]'
