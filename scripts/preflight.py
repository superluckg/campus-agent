"""只读预检：不安装、不下载、不创建Docker资源。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def result(name: str, ok: bool, detail: str) -> bool:
    print("[%s] %-24s %s" % ("OK" if ok else "缺失", name, detail))
    return ok


def main() -> int:
    checks = []
    checks.append(result("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0]))
    checks.append(result(".env", (ROOT / ".env").is_file(), str(ROOT / ".env")))
    checks.append(
        result(
            "前端依赖",
            (ROOT / "frontend" / "node_modules" / "vite" / "package.json").is_file(),
            "需要先手动安装前端依赖",
        )
    )
    checks.append(result("Docker CLI", shutil.which("docker") is not None, shutil.which("docker") or "未找到"))
    raw_root = ROOT / "data" / "knowledge_base" / "raw"
    raw_count = sum(
        path.is_file() and path.suffix.casefold() in {".pdf", ".docx", ".pptx", ".md", ".txt"}
        for path in raw_root.rglob("*")
    )
    checks.append(result("公共资料", raw_count > 0, "%d个可支持文件" % raw_count))
    if shutil.which("docker"):
        completed = subprocess.run(
            ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        images = completed.stdout
        checks.append(result("pgvector镜像", "pgvector/pgvector:pg16" in images, "只读检查"))
        checks.append(result("redis镜像", "redis:8.10.1" in images, "只读检查"))
    required_env = [
        "DEEPSEEK_API_KEY",
        "TYPESAFE_API_KEY",
    ]
    missing_env = [name for name in required_env if not os.getenv(name)]
    print("说明：本地后端启动脚本读取.env，本脚本不会读取或打印密钥。")
    if missing_env:
        print("当前进程未设置：%s" % "、".join(missing_env))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
