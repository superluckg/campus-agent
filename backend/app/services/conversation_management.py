"""永久删除自己的会话及上传副本，保留长期记忆和 Trace。"""

import logging
import stat
from pathlib import Path

from sqlalchemy import select

from app.db.identifiers import as_uuid
from app.db.models import Conversation, Trace
from app.db.session import Database
from app.services.conversation_activity import ConversationActivity


logger = logging.getLogger(__name__)


class ConversationFileError(RuntimeError):
    pass


class ConversationManagementService:
    def __init__(self, database: Database, activity: ConversationActivity,
                 upload_root: Path, redis) -> None:
        self.database = database
        self.activity = activity
        self.upload_root = upload_root.resolve()
        self.redis = redis

    def _paths(self, conversation_id: str) -> tuple[list[Path], list[Path]]:
        directory = self.upload_root / str(as_uuid(conversation_id))
        files: list[Path] = []
        directories: list[Path] = []

        def validate(path: Path) -> None:
            # 拒绝符号链接和 Windows junction，检查后才进入子目录。
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
            if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ConversationFileError("上传目录含链接，无法安全删除。")
            if not path.resolve().is_relative_to(directory) or not path.resolve().is_relative_to(self.upload_root):
                raise ConversationFileError("上传文件路径越界，无法删除。")

        def walk(path: Path) -> None:
            validate(path)
            if path.is_dir():
                directories.append(path)
                for child in sorted(path.iterdir()):
                    walk(child)
            elif path.is_file():
                files.append(path)
            else:
                raise ConversationFileError("上传目录含未知文件类型，无法删除。")

        if directory.exists() or directory.is_symlink():
            walk(directory)
        return files, directories

    async def delete(self, user_id: str, conversation_id: str) -> None:
        # 先校验所有权，避免向其他用户暴露会话是否正在工作。
        async with self.database.session() as session:
            owned = await session.scalar(select(Conversation.conversation_id).where(
                Conversation.conversation_id == as_uuid(conversation_id),
                Conversation.user_id == as_uuid(user_id),
            ))
        if owned is None:
            raise LookupError("对话不存在。")

        with self.activity.deletion(conversation_id):
            removed: list[tuple[Path, bytes]] = []
            try:
                async with self.database.session() as session:
                    conversation = await session.scalar(select(Conversation).where(
                        Conversation.conversation_id == as_uuid(conversation_id),
                        Conversation.user_id == as_uuid(user_id),
                    ).with_for_update())
                    if conversation is None:
                        raise LookupError("对话不存在。")
                    request_ids = list((await session.scalars(select(Trace.request_id).where(
                        Trace.conversation_id == as_uuid(conversation_id),
                        Trace.user_id == as_uuid(user_id),
                    ))).all())
                    # 所有路径和读取先成功，再开始删除；提交失败时用内存副本恢复文件。
                    files, directories = self._paths(conversation_id)
                    backups = [(path, path.read_bytes()) for path in files]
                    for path, content in backups:
                        path.unlink()
                        removed.append((path, content))
                    await session.delete(conversation)
                    await session.flush()
            except BaseException:
                for path, content in removed:
                    path.write_bytes(content)
                raise

            # 只移除事先检查过的空目录，不扫描或删除后来出现的新文件。
            for directory in reversed(directories):
                try:
                    directory.rmdir()
                except OSError:
                    logger.warning("会话 %s 已删除，空上传目录未清理。", conversation_id)
            keys = ["recent_messages:%s" % conversation_id]
            for request_id in request_ids:
                keys.extend("sse:{%s}:%s" % (request_id, suffix)
                            for suffix in ("events", "counter", "terminal"))
            try:
                await self.redis.client.delete(*keys)
            except Exception:
                # 缓存按 TTL 过期；已删除会话的鉴权会阻止重新读取。
                logger.warning("会话 %s 已删除，易失缓存将按 TTL 清理。", conversation_id)
