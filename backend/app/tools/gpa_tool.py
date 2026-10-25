"""GPACalculatorService 的 LangChain Tool Adapter。"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict

from .gpa_calculator import GPACalculatorService


class GPACalculatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str


def create_gpa_calculator_tool(service: GPACalculatorService) -> StructuredTool:
    async def run(file_id: str):
        result = service.calculate(file_id)
        artifact = {
            "gpa": str(result.gpa),
            "included_courses": result.included_courses,
            "excluded_courses": result.excluded_courses,
            "total_credits": str(result.total_credits),
        }
        return result.user_text(), artifact

    return StructuredTool.from_function(
        coroutine=run,
        name="gpa_calculator",
        description=(
            "读取XLSX成绩单并按项目演示规则确定性计算GPA。"
            "需要成绩单包含课程名称、学分、成绩、课程类别和修读状态。"
        ),
        args_schema=GPACalculatorInput,
        response_format="content_and_artifact",
    )

