"""单 worker 下的会话活动登记：覆盖排队、上传、标题和记忆后台任务。"""

from collections import Counter
from contextlib import contextmanager
from typing import Callable, Iterator


class ConversationBusyError(RuntimeError):
    pass


class ConversationActivity:
    def __init__(self) -> None:
        self._active: Counter[str] = Counter()
        self._deleting: set[str] = set()

    def is_busy(self, conversation_id: str) -> bool:
        return bool(self._active[conversation_id]) or conversation_id in self._deleting

    def reserve(self, conversation_id: str) -> Callable[[], None]:
        if conversation_id in self._deleting:
            raise ConversationBusyError("该对话正在删除，请稍后重试。")
        self._active[conversation_id] += 1
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._active[conversation_id] -= 1
                if not self._active[conversation_id]:
                    del self._active[conversation_id]

        return release

    @contextmanager
    def operation(self, conversation_id: str) -> Iterator[None]:
        release = self.reserve(conversation_id)
        try:
            yield
        finally:
            release()

    @contextmanager
    def deletion(self, conversation_id: str) -> Iterator[None]:
        if self.is_busy(conversation_id):
            raise ConversationBusyError("该对话正在生成、上传或整理，请完成后再删除。")
        self._deleting.add(conversation_id)
        try:
            yield
        finally:
            self._deleting.discard(conversation_id)
