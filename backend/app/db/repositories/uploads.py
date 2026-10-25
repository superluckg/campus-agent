"""会话级上传文件元数据和抽取文本。"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select

from app.db.models import UploadedFile, UploadedFileContent
from app.db.identifiers import as_uuid
from app.db.session import Database


class UploadRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def count(self, user_id: str, conversation_id: str) -> int:
        async with self.database.session() as session:
            result = await session.execute(
                select(func.count(UploadedFile.file_id)).where(
                    UploadedFile.user_id == as_uuid(user_id),
                    UploadedFile.conversation_id == as_uuid(conversation_id),
                )
            )
            return int(result.scalar_one())

    async def create(
        self,
        user_id: str,
        conversation_id: str,
        original_filename: str,
        relative_path: str,
        extension: str,
        mime_type: Optional[str],
        size_bytes: int,
        content_hash: str,
    ) -> UploadedFile:
        item = UploadedFile(
            user_id=as_uuid(user_id),
            conversation_id=as_uuid(conversation_id),
            original_filename=original_filename,
            relative_path=relative_path,
            extension=extension,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_hash=content_hash,
            parse_status="uploaded",
        )
        async with self.database.session() as session:
            session.add(item)
            await session.flush()
        return item

    async def save_extracted_text(
        self,
        file_id: str,
        extracted_text: str,
        page_map: dict,
    ) -> None:
        async with self.database.session() as session:
            item = await session.get(UploadedFile, as_uuid(file_id))
            if item is None:
                raise ValueError("上传文件不存在。")
            content = await session.get(UploadedFileContent, as_uuid(file_id))
            if content is None:
                session.add(
                    UploadedFileContent(
                        file_id=as_uuid(file_id),
                        extracted_text=extracted_text,
                        page_map=page_map,
                    )
                )
            else:
                content.extracted_text = extracted_text
                content.page_map = page_map
            item.parse_status = "parsed"
            item.parse_error = None

    async def mark_failed(self, file_id: str, error: str) -> None:
        async with self.database.session() as session:
            item = await session.get(UploadedFile, as_uuid(file_id))
            if item is not None:
                item.parse_status = "failed"
                item.parse_error = error[:2000]

    async def list_owned(self, user_id: str, conversation_id: str) -> list[UploadedFile]:
        async with self.database.session() as session:
            result = await session.execute(
                select(UploadedFile)
                .where(
                    UploadedFile.user_id == as_uuid(user_id),
                    UploadedFile.conversation_id == as_uuid(conversation_id),
                )
                .order_by(UploadedFile.created_at)
            )
            return list(result.scalars())

    async def load_texts(
        self,
        user_id: str,
        conversation_id: str,
        file_ids: list[str],
    ) -> list[tuple[UploadedFile, UploadedFileContent]]:
        query = (
            select(UploadedFile, UploadedFileContent)
            .join(UploadedFileContent, UploadedFileContent.file_id == UploadedFile.file_id)
            .where(
                UploadedFile.user_id == as_uuid(user_id),
                UploadedFile.conversation_id == as_uuid(conversation_id),
                UploadedFile.file_id.in_([as_uuid(value) for value in file_ids]),
            )
        )
        async with self.database.session() as session:
            return list((await session.execute(query)).tuples())
