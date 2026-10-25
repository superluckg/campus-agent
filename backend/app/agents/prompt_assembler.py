"""按 Global → Agent → Skills → Task Context 组装 Prompt。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from app.schemas.enums import AgentName
from app.skills.models import SelectedSkill


_AGENT_PROMPTS = {
    AgentName.CAREER: "career_agent.md",
    AgentName.CAMPUS: "campus_agent.md",
    AgentName.LEARNING: "learning_agent.md",
}


class PromptAssembler:
    def __init__(self, prompt_dir: Path) -> None:
        self.prompt_dir = prompt_dir
        self.global_template = (prompt_dir / "global_system.md").read_text(encoding="utf-8")

    def assemble(
        self,
        agent_name: AgentName,
        task_focus: str,
        conversation_summary: str,
        long_term_memories: Iterable[str],
        selected_skills: Iterable[SelectedSkill],
        available_files: Iterable[dict[str, str]],
    ) -> str:
        memories = list(long_term_memories)
        skills = list(selected_skills)
        files = list(available_files)
        global_prompt = self.global_template.format(
            task_focus=task_focus,
            conversation_summary=conversation_summary or "无",
            long_term_memories="\n".join("- %s" % item for item in memories) or "无",
            selected_skills="\n\n".join(
                "## %s\n%s" % (skill.display_name, skill.content)
                for skill in skills
            )
            or "本轮未注入额外Skill。",
            available_files="\n".join(
                "- %s（file_id=%s，格式=%s，解析状态=%s）"
                % (
                    item.get("filename", "未命名"),
                    item.get("file_id", ""),
                    item.get("extension", ""),
                    item.get("parse_status", ""),
                )
                for item in files
            )
            or "无",
        )
        agent_prompt = (self.prompt_dir / _AGENT_PROMPTS[agent_name]).read_text(
            encoding="utf-8"
        )
        return "%s\n\n%s" % (global_prompt.strip(), agent_prompt.strip())
