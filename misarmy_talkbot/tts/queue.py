import asyncio
import discord
from .audio import AudioMessage, AudioState


class MessageQueue:
    def __init__(self):
        self.__items: list[AudioMessage] = []
        self.__condition = asyncio.Condition()

    async def put(self, item: AudioMessage):
        self.__items.append(item)
        item._on_state_change = self._notify
        await self._notify()

    async def remove(self, item: AudioMessage | discord.Message):
        if isinstance(item, AudioMessage):
            self.__items.remove(item)
        else:
            for message in self.__items:
                if message.original == item:
                    self.__items.remove(message)
                    break
        await self._notify()

    async def clear(self):
        self.__items.clear()
        await self._notify()

    async def get(self, state: AudioState, index: int | None = None) -> AudioMessage:
        """Wait for a message with the given state at the specified index (any index if not provided)."""
        if index is not None:
            while len(self.__items) == 0 or self.__items[index].state != state:
                async with self.__condition:
                    await self.__condition.wait()
            return self.__items.pop(index)

        while True:
            item = next((item for item in self.__items if item.state == state), None)
            if item is not None:
                self.__items.remove(item)
                return item
            async with self.__condition:
                await self.__condition.wait()

    async def _notify(self):
        async with self.__condition:
            self.__condition.notify_all()

    def __getitem__(self, index: int):
        return self.__items[index]

    def __setitem__(self, index: int, message: AudioMessage):
        self.__items[index] = message

    def __contains__(self, item):
        return item in self.__items

    def __iter__(self):
        return self.__items.__iter__()

    def __len__(self):
        return len(self.__items)

    def __repr__(self) -> str:
        return f'[{', '.join([str(x) for x in self.__items])}]'
