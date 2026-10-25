"""从主存储加载全局图需要的短期上下文。"""

from __future__ import annotations

from app.db.repositories.conversations import ConversationRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.uploads import UploadRepository
from app.cache.recent_messages import RecentMessageCache


class PostgresContextLoader:
    def __init__(
        self,
        conversations: ConversationRepository,
        memories: MemoryRepository,
        uploads: UploadRepository,
        recent_cache: RecentMessageCache,
    ) -> None:
        self.conversations = conversations
        self.memories = memories
        self.uploads = uploads
        self.recent_cache = recent_cache

    async def load_recent(self, user_id: str, conversation_id: str) -> list[dict[str, str]]:
        del user_id
        cached = await self.recent_cache.get(conversation_id)
        # 近期消息不足窗口时需要从数据库回填，不能仅凭非空就信任。
        # 短会话也回查主存储，完整窗口仍保留缓存命中的快速路径。
        keep_count = self.recent_cache.keep_count
        if len(cached) >= keep_count:
            return cached[-keep_count:]
        messages = await self.conversations.list_messages(conversation_id, limit=keep_count)
        values = [{"role": item.role, "content": item.content} for item in messages]
        await self.recent_cache.replace(conversation_id, values)
        return values

    async def load_summary(self, conversation_id: str) -> str:
        return await self.memories.latest_summary(conversation_id)

    async def load_uploaded_files(
        self,
        user_id: str,
        conversation_id: str,
    ) -> list[dict[str, str]]:
        files = await self.uploads.list_owned(user_id, conversation_id)
        return [
            {
                "file_id": str(item.file_id),
                "filename": item.original_filename,
                "relative_path": item.relative_path,
                "extension": item.extension,
                "parse_status": item.parse_status,
            }
            for item in files
        ]
