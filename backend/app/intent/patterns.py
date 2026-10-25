"""只承担兜底作用的关键词与结构化实体匹配。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List

from app.schemas.enums import IntentName

from .signals import IntentSignal

_PATTERNS: Dict[IntentName, tuple[str, ...]] = {
    IntentName.CAREER_RESUME: ("简历", "求职材料", "项目经历"),
    IntentName.CAREER_RECRUITMENT: ("招聘会", "双选会", "校招", "就业通知"),
    IntentName.CAMPUS_NETWORK: ("校园网", "网络充值", "网络套餐", "认证失败"),
    IntentName.CAMPUS_AFFAIRS: ("学籍", "选课", "缓考", "毕业条件", "考试规定"),
    IntentName.LEARNING_DOCUMENT: ("讲义", "笔记", "这份文档", "上传的资料"),
    IntentName.LEARNING_PLAN: ("学习计划", "复习计划", "备考计划"),
    IntentName.LEARNING_GPA: ("gpa", "绩点", "成绩单"),
    IntentName.SMALL_TALK: ("你好", "您好", "谢谢", "你能做什么", "讲个笑话", "你是谁", "你是什么模型"),
}


class PatternIntentMatcher:
    def match(
        self,
        query: str,
        uploaded_file_ids: Iterable[str] = (),
        uploaded_filenames: Iterable[str] = (),
    ) -> IntentSignal:
        normalized = query.casefold()
        scores: Dict[IntentName, float] = {}
        matched_domains: set[str] = set()
        for intent, keywords in _PATTERNS.items():
            matches = sum(keyword.casefold() in normalized for keyword in keywords)
            if matches:
                scores[intent] = min(1.0, 0.55 + 0.15 * (matches - 1))
                matched_domains.add(intent.value.split("_", 1)[0])
        if len(matched_domains & {"career", "campus", "learning"}) >= 2:
            scores[IntentName.COMPOUND] = 0.9
        # 寒暄夹带业务时，关键词信号优先支持业务。
        if matched_domains & {"career", "campus", "learning"}:
            scores.pop(IntentName.SMALL_TALK, None)
        if not scores and re.fullmatch(r"(?:hi|hello|hey|嗨|在吗)[\s~～!！?？.。]*", normalized):
            scores[IntentName.SMALL_TALK] = 0.8

        file_ids = list(uploaded_file_ids)
        filenames = list(uploaded_filenames)
        entities: Dict[str, List[str]] = {}
        mentioned_files = [
            name for name in filenames if self._positive_file_mention(normalized, Path(name).name.casefold())
        ]
        if mentioned_files:
            entities["filename"] = mentioned_files
            entities["file_id"] = [
                file_id
                for file_id, filename in zip(file_ids, filenames)
                if filename in mentioned_files
            ]
        elif len(file_ids) == 1:
            entities["file_id"] = file_ids
        dates = re.findall(
            r"(?:今天|明天|本周|这周|下周|\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?)",
            query,
        )
        if dates:
            entities["date"] = list(dict.fromkeys(dates))
        return IntentSignal(source="pattern", scores=scores, entities=entities)

    @staticmethod
    def _positive_file_mention(query: str, filename: str) -> bool:
        for match in re.finditer(re.escape(filename), query):
            prefix = re.split(r"[，,。；;!?！？]", query[:match.start()])[-1]
            if not re.search(r"(?:不用|不要|无需|别|跳过|忽略|排除|不看)[^，,。；;!?！？]{0,20}$", prefix):
                return True
        return False
