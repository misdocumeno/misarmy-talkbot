import asyncio
from collections.abc import Iterator

import discord

from misarmy_talkbot.core.playback.audio import AudioMessage, AudioState
from misarmy_talkbot.observability.trace import step


class MessageQueue:
    def __init__(self, guild_id: int) -> None:
        self._guild_id = guild_id
        self.__items: list[AudioMessage] = []
        self.__condition = asyncio.Condition()

    async def put(self, item: AudioMessage) -> None:
        step(
            self._guild_id,
            'queue',
            'put',
            'ENTER',
            content=item.content,
            depth_before=len(self.__items),
        )
        self.__items.append(item)
        item._on_state_change = self._notify
        await self._notify()
        step(
            self._guild_id,
            'queue',
            'put',
            'EXIT',
            depth_after=len(self.__items),
        )

    async def insert_head(self, item: AudioMessage) -> None:
        step(
            self._guild_id,
            'queue',
            'insert_head',
            'ENTER',
            content=item.content,
        )
        self.__items.insert(0, item)
        item._on_state_change = self._notify
        await self._notify()
        step(
            self._guild_id,
            'queue',
            'insert_head',
            'EXIT',
            depth=len(self.__items),
        )

    async def remove(self, item: AudioMessage | discord.Message) -> None:
        step(
            self._guild_id,
            'queue',
            'remove',
            'ENTER',
            depth_before=len(self.__items),
        )
        if isinstance(item, AudioMessage):
            self.__items.remove(item)
        else:
            for message in self.__items:
                if message.original == item:
                    self.__items.remove(message)
                    break
        await self._notify()
        step(
            self._guild_id,
            'queue',
            'remove',
            'EXIT',
            depth_after=len(self.__items),
        )

    async def clear(self) -> None:
        step(
            self._guild_id,
            'queue',
            'clear',
            'ENTER',
            depth_before=len(self.__items),
        )
        self.__items.clear()
        await self._notify()
        step(self._guild_id, 'queue', 'clear', 'EXIT')

    async def wait_until_head(self, state: AudioState) -> AudioMessage:
        """Block until ``__items[0]`` exists and is in ``state`` (does not remove it)."""
        step(
            self._guild_id,
            'queue',
            'wait_head',
            'ENTER',
            want_state=state.name,
        )
        while len(self.__items) == 0 or self.__items[0].state != state:
            head_state = self.__items[0].state.name if self.__items else None
            step(
                self._guild_id,
                'queue',
                'wait_head',
                'POINT',
                point='blocked',
                want_state=state.name,
                queue_len=len(self.__items),
                head_state=head_state,
            )
            async with self.__condition:
                await self.__condition.wait()
        step(
            self._guild_id,
            'queue',
            'wait_head',
            'EXIT',
            head_content=self.__items[0].content,
            head_state=self.__items[0].state.name,
        )
        return self.__items[0]

    async def get(
        self, state: AudioState, index: int | None = None
    ) -> AudioMessage:
        step(
            self._guild_id,
            'queue',
            'get',
            'ENTER',
            want_state=state.name,
            index=index,
        )
        if index is not None:
            while len(self.__items) == 0 or self.__items[index].state != state:
                async with self.__condition:
                    await self.__condition.wait()
            item = self.__items.pop(index)
            step(
                self._guild_id,
                'queue',
                'get',
                'EXIT',
                content=item.content,
                depth=len(self.__items),
            )
            return item

        while True:
            item = next((x for x in self.__items if x.state == state), None)
            if item is not None:
                self.__items.remove(item)
                step(
                    self._guild_id,
                    'queue',
                    'get',
                    'EXIT',
                    content=item.content,
                    depth=len(self.__items),
                )
                return item
            async with self.__condition:
                await self.__condition.wait()

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

    def __repr__(self) -> str:
        return f'[{", ".join([str(x) for x in self.__items])}]'
