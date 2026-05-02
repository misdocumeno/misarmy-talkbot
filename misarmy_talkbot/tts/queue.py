import asyncio
import discord
from .audio import AudioMessage, AudioState
from ..logger import logger


class MessageQueue:
    def __init__(self):
        self.__items: list[AudioMessage] = []
        self.__condition = asyncio.Condition()

    def debug_items_identity_raw(self) -> str:
        """Low-level: AudioMessage object ids + Discord message ids + state (order is queue order)."""
        parts = [
            f'(audio_msg_obj_id={id(a)},orig_msg_id={a.original.id},state={a.state.name})' for a in self.__items
        ]
        return f'queue_obj_id={id(self)} condition_obj_id={id(self.__condition)} items_RAW={parts}'

    async def put(self, item: AudioMessage):
        self.__items.append(item)
        item._on_state_change = self._notify
        logger.debug(
            f'MessageQueue.put msg_id={item.original.id} audio_msg_obj_id={id(item)} state={item.state} '
            f'queue_len={len(self.__items)} | {self.debug_items_identity_raw()}'
        )
        await self._notify()

    async def remove(self, item: AudioMessage | discord.Message):
        if isinstance(item, AudioMessage):
            self.__items.remove(item)
            logger.debug(
                f'MessageQueue.remove AudioMessage msg_id={item.original.id} queue_len_after={len(self.__items)}'
            )
        else:
            for message in self.__items:
                if message.original == item:
                    self.__items.remove(message)
                    logger.debug(
                        f'MessageQueue.remove by Message msg_id={item.id} queue_len_after={len(self.__items)}'
                    )
                    break
        await self._notify()

    async def clear(self):
        n = len(self.__items)
        self.__items.clear()
        logger.debug(f'MessageQueue.clear removed={n}')
        await self._notify()

    def _queue_state_snapshot(self, max_items: int = 12) -> str:
        chunk = self.__items[:max_items]
        parts = [f'pos_{i} msg={a.original.id} {a.state.name}' for i, a in enumerate(chunk)]
        if len(self.__items) > max_items:
            parts.append(f'... +{len(self.__items) - max_items} more')
        return '[' + ', '.join(parts) + ']'

    async def get(self, state: AudioState, index: int | None = None) -> AudioMessage:
        """Wait for a message with the given state at the specified index (any index if not provided)."""
        if index is not None:
            logged_empty = False
            logged_stall = False
            logged_starve = False
            while len(self.__items) == 0 or self.__items[index].state != state:
                if len(self.__items) == 0:
                    if not logged_empty:
                        logger.debug(f'MessageQueue.wait (empty) want={state.name} idx={index}')
                        logged_empty = True
                elif self.__items[index].state != state:
                    head = self.__items[index].state.name
                    deeper_ready = any(
                        self.__items[j].state == state for j in range(index + 1, len(self.__items))
                    )
                    if deeper_ready:
                        if not logged_starve:
                            logger.warning(
                                f'MessageQueue HEAD_OF_LINE_BLOCK want={state.name} at_idx={index} '
                                f'head_state={head} — READY items exist behind head; _speak stalls until head '
                                f'becomes READY. snapshot={self._queue_state_snapshot()}'
                            )
                            logged_starve = True
                    elif not logged_stall:
                        logger.debug(
                            f'MessageQueue.wait idx={index} want={state.name} head={head} '
                            f'qlen={len(self.__items)} snapshot={self._queue_state_snapshot()}'
                        )
                        logged_stall = True
                async with self.__condition:
                    await self.__condition.wait()
            got = self.__items.pop(index)
            logger.debug(
                f'MessageQueue.get idx={index} msg_id={got.original.id} audio_msg_obj_id={id(got)} '
                f'got_state={got.state} want={state} queue_len_after={len(self.__items)} | '
                f'{self.debug_items_identity_raw()}'
            )
            return got

        while True:
            item = next((item for item in self.__items if item.state == state), None)
            if item is not None:
                self.__items.remove(item)
                logger.debug(
                    f'MessageQueue.get(any) msg_id={item.original.id} state={state} '
                    f'queue_len_after={len(self.__items)}'
                )
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
