"""只允许 Agent 访问当前会话已经登记的上传文件。"""

from __future__ import annotations

from pathlib import Path


class ScopedUploadResolver:
    def __init__(self, upload_root: Path, files: list[dict[str, str]]) -> None:
        self.upload_root = upload_root.resolve()
        self.allowed = {
            item["file_id"]: item["relative_path"]
            for item in files
            if item.get("file_id") and item.get("relative_path")
        }

    def resolve(self, file_id: str) -> Path:
        relative_path = self.allowed.get(file_id)
        if relative_path is None:
            raise ValueError("文件不属于当前会话或不存在。")
        resolved = (self.upload_root / relative_path).resolve()
        try:
            resolved.relative_to(self.upload_root)
        except ValueError as exc:
            raise ValueError("上传文件路径越界。") from exc
        if not resolved.is_file():
            raise ValueError("上传文件不存在于磁盘。")
        return resolved
