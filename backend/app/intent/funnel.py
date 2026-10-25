"""四级漏斗：范围证据、任务一致性与独立向量缓存。"""

from __future__ import annotations

import asyncio
import re
import time

import numpy as np

from app.core.errors import ProviderStopped
from app.intent.decision_helpers import early_result, optimized_questions, vector_accept
from app.intent.diagnostics import attach_diagnostics, complete_result
from app.intent.jev import recognize_jev
from app.intent.signals import IntentSignal
from app.schemas.enums import IntentName

# 只决定升级，不直接判断拒绝或把文本改写成业务。
BOUNDARY = re.compile(
    r"股票|炒股|基金|期货|证券|买入|卖出|理财|投资|订票|订酒店|预订.{0,8}(?:酒店|机票)|"
    r"实时天气|气象预警|家庭宽带|家用.{0,4}(?:网络|光纤)|"
    r"(?:编写|开发|实现|修改|解释).{0,8}(?:源码|源代码|程序|脚本)|"
    r"api.?key|密钥|token|凭证|提示词|后台秘密|破解|绕过|盗取|偷|他人|别人|同学的|未经授权",
    re.I,
)
DEPENDENT = re.compile(
    r"这个|那个|这份|那份|它|刚才|之前|继续|接着|上面|前面|前者|后者|"
    r"同一个|同样的事|同样的申请|那研究生|那本科生"
)
FILES = re.compile(r"附件|上传|文件|资料|讲义|笔记|提纲|简历|履历|成绩单|这份|那份")
EXCLUSION = re.compile(r"不要|不用|不是|并非|除了|排除|不需要|别(?:看|读|做|分析|查询|处理)|先不")
META = re.compile(r"我一共|说过几|问过几|举例|引用|只是提到|这段对话|这段聊天|他说|她说|转述")
MULTI = re.compile(r"同时|另外|然后|以及|再帮|并帮|还要|再(?:给|制定|安排|查询|计算)")
CAPABILITIES = {
    IntentName.CAMPUS_NETWORK: r"校园网|学校网络|宿舍网络|校内网络",
    IntentName.CAMPUS_AFFAIRS: (
        r"缓考|免修|选课|退选|转专业|学籍|毕业|学分|重修|补考|休学|复学|"
        r"学校.{0,8}(?:手续|制度|规定|流程)"
    ),
    IntentName.CAREER_RECRUITMENT: (
        r"招聘|招募|校招|双选|宣讲|就业(?:通知|中心|服务|活动|平台|网站)"
    ),
    IntentName.LEARNING_PLAN: r"学习|复习|备考|自学|(?:考研|期末|考试).{0,8}(?:计划|安排|进度)",
}
POLITE = (
    r"(?:(?:你好[，,]?|您好[，,]?|请问|请|麻烦你?|帮我|我想了解|我想查一下|给我)"
    r"(?:一下|查一下|查询|看看|问一下)?)*"
)
NETWORK = r"(?:校园网|学校网络|宿舍网络|校内网络)"
EVENT = r"(?:招聘会|双选会|校招活动|企业宣讲|宣讲会|实习招聘通知|实习招募信息)"
SCHOOL = r"(?:我们学校|学校|校内|校内就业网站上|学校就业平台)?"
PERIOD = r"(?:最近|近期|本周|这周|下周|本月|这个月)?"
NUMBER = r"[一二三四五六七八九十两0-9]+"
RULES = {
    IntentName.SMALL_TALK: (
        r"(?:你好|您好|嗨|hi|hello|hey|早上好|下午好|晚上好|在吗|再见|晚安)(?:呀|啊|哦|呢)?",
        r"(?:你好|您好|嗨|早上好|下午好|晚上好)[，,]?(?:很高兴认识你|见到你真开心)",
        r"(?:谢谢|多谢)(?:你|你的帮助|你的解释)?(?:呀|啊|啦)?",
        r"(?:真是|真)?(?:帮大忙了|太感谢了)[，,]?(?:谢谢|多谢)(?:你|你呀)?",
        r"(?:请问|请|可以|能否)?(?:介绍一下|说说)?"
        r"(?:你的身份|你是谁|你是什么模型|你能做什么|你有哪些功能|你可以帮我做什么|你能帮我做些什么)"
        r"(?:吗|呢)?",
    ),
    IntentName.CAMPUS_NETWORK: (
        POLITE + NETWORK + r"(?:账号|账户)?(?:怎么|如何|怎样)"
        r"(?:充值|连接|登录|开通|办理|注销|停用|认证|报修)",
        POLITE + NETWORK + r"(?:连接|登录|认证)(?:失败|不上|不了)(?:怎么办|如何解决)?",
        POLITE + NETWORK + r"(?:的)?(?:资费|套餐|收费标准|充值方式|账户注销流程)"
        r"(?:是什么|有哪些|是多少|怎么查询)?",
        POLITE
        + r"(?:我(?:自己)?的)?"
        + NETWORK
        + r"(?:账号)?(?:忘记|忘了)密码(?:了)?(?:怎么|如何)(?:找回|重置)",
        POLITE + NETWORK + r"密码(?:忘记|忘了)(?:了)?(?:应该)?(?:怎么办|怎么找回|如何重置)",
        POLITE + NETWORK + r"(?:总说|提示)?(?:认证超时|掉线|断网|认证失败)"
        r"[，,]?(?:我(?:该|应该)?|应该)?(?:怎么|如何)(?:处理|排查|解决|报修)",
        POLITE + NETWORK + r"(?:认证页面|认证页)(?:一直)?(?:转圈|打不开|没有响应)"
        r"[，,]?(?:怎么|如何)(?:处理|排查|解决)",
    ),
    IntentName.CAREER_RECRUITMENT: (
        POLITE + PERIOD + SCHOOL + PERIOD + r"(?:有|有什么|有哪些)" + EVENT + r"(?:吗|呢)?",
        POLITE
        + r"(?:查询|查看|查)?"
        + PERIOD
        + SCHOOL
        + PERIOD
        + EVENT
        + r"(?:安排|信息|时间|报名入口)?",
        POLITE + SCHOOL + PERIOD + r"(?:发布了哪些|发布的)" + EVENT,
        POLITE + r"企业来学校开宣讲[，,]?(?:我)?(?:在)?哪里看(?:日程|时间)(?:和报名方式)?",
        POLITE + r"就业中心(?:能)?(?:从)?哪里查询企业来校招聘的信息",
    ),
    IntentName.CAMPUS_AFFAIRS: (
        POLITE + r"(?:学校)?(?:本科)?毕业(?:的)?(?:条件|学分要求|必修课要求)"
        r"(?:是什么|有哪些|是多少)?",
        POLITE + r"(?:缓考|免修|转专业|学籍异动|休学|复学)(?:申请)?"
        r"(?:需要什么材料|有什么条件|有哪些条件|怎么申请|如何申请|怎么办理|有哪些手续|流程是什么)",
        POLITE + r"(?:我们学校|学校)?(?:本科生|研究生)?(?:英语|英语课|英语课程)?"
        r"(?:免修|转专业)(?:的)?(?:条件是什么|条件有哪些|需要什么条件|要满足哪些学校条件)",
        POLITE + r"申请(?:本科生|研究生)?(?:英语课|英语课程)?(?:免修|转专业)"
        r"(?:需要什么条件|要满足哪些学校条件|要满足什么条件)",
        POLITE + r"(?:学校)?(?:本科|研究生)?毕业对(?:必修课|总学分|学分)"
        r"(?:和(?:总学分|必修课))?有什么要求",
        POLITE + r"(?:因身体原因)?(?:我)?想休学(?:一个学期)?[，,]?需要(?:交)?哪些材料",
    ),
    IntentName.LEARNING_PLAN: (
        POLITE
        + r"(?:制定|安排|规划)(?:一个|一份)?(?:未来|接下来)?"
        + r"(?:"
        + NUMBER
        + r"(?:天|周|个月)的?)?(?:学习|复习|备考)计划",
        POLITE + r"(?:制定|安排)(?:一份)?[^，。！？,;；]{0,18}(?:复习|备考|学习)(?:计划|日程)",
        NUMBER + r"(?:天|周|个月)(?:后)?考[^，。！？,;；]{1,12}[，,]"
        r"每天(?:能学|能复习)?" + NUMBER + r"(?:个)?小时[，,]"
        r"(?:帮我|请)?(?:安排|制定)(?:复习进度|学习计划|备考计划)",
        r"准备[^，。！？,;；]{1,12}考试[，,]给我(?:一个|一份)?"
        + NUMBER
        + r"(?:天|周|个月)的每日学习安排",
    ),
}
NEGATIVES = {
    "campus_network": ["投资通信公司的证券", "开发网络认证软件", "预订网络运营商的出差酒店"],
    "campus_affairs": ["购买大学教育企业的基金", "获取其他学生的个人档案"],
    "career_recruitment": ["代购参加就业活动的车票", "分析上传的求职履历"],
    "learning_plan": ["总结课程文件中的知识点", "从成绩记录计算平均分和绩点"],
}


def fast_gate(query):
    # 月份/学期范围是完整时间信息，不因包含“这个”就当作指代。
    calendar = re.sub(r"这个(?:月|学期|星期|礼拜)", "当前时段", query)
    for pattern, reason in (
        (BOUNDARY, "业务范围或授权需要模型核验"),
        (META, "引用或会话回顾需要解析实际任务"),
        (EXCLUSION, "否定与排除需要语义判断"),
        (DEPENDENT, "任务依赖上下文或文件指代"),
        (FILES, "用户提到文件用途，需确认附件选择"),
        (MULTI, "多个交付物需要判断任务组合"),
    ):
        if pattern.search(calendar):
            return reason
    if len(query.strip().rstrip("！？?!。.")) <= 4:
        return "短输入可能只是主题词，需要确认任务"
    if len(query) > 240:
        return "长请求需要完整语义判断"
    domains = {
        intent.value.split("_", 1)[0]
        for intent, expression in CAPABILITIES.items()
        if re.search(expression, query)
    }
    if len(domains) > 1:
        return "涉及多个Agent领域"
    return None


def rule_decision(service, query):
    normalized = query.casefold().strip().rstrip(" \t\r\n~～!！?？.。")
    for intent, patterns in RULES.items():
        if service.catalog[intent].required_entities:
            continue
        if any(re.fullmatch(pattern, normalized) for pattern in patterns):
            return intent
    return None


def decision_questions(catalog, files):
    result = optimized_questions(catalog, files)
    result["intent"]["instructions"] = result["intent"]["instructions"].replace(
        "unclear means the domain cannot be established even with context.",
        "unclear means the specific supported intent cannot be determined using context, "
        "even when the broad domain is known.",
    )
    result["intent"]["instructions"] += (
        " First resolve the current requested deliverable, then negated auxiliary materials. "
        "An explicitly requested GPA calculation remains learning_gpa "
        "if the spreadsheet is missing; "
        "resume revision remains career_resume if the resume is not yet uploaded. "
        "Knowing a broad domain alone does not identify a task or imply missing files. "
        "Use unclear if the specific intent is undetermined after resolving context; "
        "do not use unclear merely because a clearly requested task lacks required input."
    )
    for key, question in result.items():
        if key.startswith("task_"):
            question["instructions"] += (
                " Missing input does not negate an explicitly requested deliverable. "
                "Do not count possible future tasks, quotations or auxiliary reading "
                "as deliverables."
            )
        if key.startswith("file_"):
            question["instructions"]["question"] += (
                " Evaluate file suitability for the actual deliverables too: "
                "course notes are not a "
                "resume or transcript merely because they are the only upload. "
                "If the appropriate requested file is not uploaded, answer false; missing-input "
                "clarification is computed in code, not by relabeling the intent."
            )
    return result


async def embedding_decision(service, query):
    """复用样例编码；一次归一化矩阵比较代替逐样例Python余弦循环。无查询答案缓存。"""
    profile = {}
    cache_started = time.perf_counter()
    if not hasattr(service, "_example_vectors_lock"):
        service._example_vectors_lock = asyncio.Lock()
        service._example_vectors = None
    async with service._example_vectors_lock:
        if service._example_vectors is None:
            examples, keys = [], []
            config = service.hybrid_config
            for intent, definition in service.catalog.items():
                samples = list(definition.examples) + config.get("positive_examples", {}).get(
                    intent.value, []
                )
                for example in dict.fromkeys(samples):
                    examples.append(example)
                    keys.append(intent.value)
            negatives = {
                name: list(values) for name, values in config.get("negative_examples", {}).items()
            }
            for name, values in NEGATIVES.items():
                negatives.setdefault(name, []).extend(values)
            for name, values in negatives.items():
                for example in dict.fromkeys(values):
                    examples.append(example)
                    keys.append("negative:" + name)
            matrix = np.asarray(
                await service.embeddings.aembed_documents(examples), dtype=np.float64
            )
            if matrix.ndim != 2 or matrix.shape[0] != len(keys) or not np.isfinite(matrix).all():
                raise ValueError("向量样例数量、维度或数值无效")
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            if (norms == 0).any():
                raise ValueError("样例包含零向量")
            service._example_vectors = (matrix / norms, keys)
    profile["embedding_prototypes"] = (time.perf_counter() - cache_started) * 1000
    matrix, keys = service._example_vectors
    query_started = time.perf_counter()
    vector = np.asarray(await service.embeddings.aembed_query(query), dtype=np.float64)
    if vector.ndim != 1 or len(vector) != matrix.shape[1] or not np.isfinite(vector).all():
        raise ValueError("查询向量维度或数值无效")
    norm = np.linalg.norm(vector)
    if not norm:
        raise ValueError("查询包含零向量")
    profile["embedding_query"] = (time.perf_counter() - query_started) * 1000
    compare_started = time.perf_counter()
    values = np.clip(matrix @ (vector / norm), 0, 1)
    scores = {}
    for key, score in zip(keys, values, strict=True):
        scores[key] = max(scores.get(key, 0.0), float(score))
    profile["embedding_compare"] = (time.perf_counter() - compare_started) * 1000
    return (
        IntentSignal(
            "embedding",
            {
                IntentName(key): score
                for key, score in scores.items()
                if not key.startswith("negative:")
            },
            {},
        ),
        {
            key.split(":", 1)[1]: score
            for key, score in scores.items()
            if key.startswith("negative:")
        },
        profile,
    )


async def recognize(service, query, messages, summary, file_ids, filenames):
    trace, durations, errors = [], {}, {}
    started = time.perf_counter()
    blocked = fast_gate(query)
    rule = rule_decision(service, query)
    if rule == IntentName.SMALL_TALK:
        blocked = None
    durations["rule"] = (time.perf_counter() - started) * 1000
    if rule is not None and not blocked:
        return complete_result(early_result(
            rule,
            "rule",
            {rule.value: 1.0},
            [{"stage": "rule", "accepted": True, "reason": "完整句式及业务契约匹配"}],
            durations,
        ))
    trace.append({"stage": "rule", "accepted": False, "reason": blocked or "未完整匹配"})
    warmup_error = getattr(service, "_embedding_warmup_error", None)
    if not blocked and not warmup_error and any(
        re.search(pattern, query) for pattern in CAPABILITIES.values()
    ):
        started = time.perf_counter()
        try:
            signal, negatives, profile = await asyncio.wait_for(
                embedding_decision(service, query),
                service.hybrid_config.get("embedding_timeout_seconds", 3),
            )
            durations.update(profile)
            intent, detail = vector_accept(signal, negatives, service.hybrid_config)
            if intent and (
                service.catalog[intent].required_entities
                or not re.search(CAPABILITIES[intent], query)
            ):
                intent = None
                detail.update(accepted=False, reason="相似类别缺少独立领域证据或仍需必要字段")
            trace.append({"stage": "embedding", **detail})
            durations["embedding"] = (time.perf_counter() - started) * 1000
            if intent:
                return complete_result(early_result(
                    intent,
                    "embedding",
                    {key.value: score for key, score in signal.scores.items()},
                    trace,
                    durations,
                ))
        except (ProviderStopped, asyncio.CancelledError) as exc:
            durations['embedding'] = (time.perf_counter() - started) * 1000
            trace.append({'stage': 'embedding', 'status': 'failed', 'reason': '阶段被终止。'})
            attach_diagnostics(exc, trace, durations, {'embedding': type(exc).__name__}, {'embedding': 1})
            raise
        except Exception as exc:
            errors["embedding"] = type(exc).__name__
            durations["embedding"] = (time.perf_counter() - started) * 1000
            trace.append(
                {"stage": "embedding", "status": "failed", "accepted": False, "reason": "向量失败或超时，升级"}
            )
    else:
        trace.append(
            {
                "stage": "embedding",
                "skipped": True,
                "reason": blocked or (
                    f"启动向量预热失败（{warmup_error}），直接升级Jev。"
                    if warmup_error else "缺少独立领域证据"
                ),
            }
        )
    overrides = {
        **service.hybrid_config.get("jev_thresholds", {}),
        "validate_task_consistency": True,
    }
    try:
        result = await recognize_jev(
            service, query, messages, summary, file_ids, filenames,
            question_builder=decision_questions, threshold_overrides=overrides,
        )
    except BaseException as exc:
        attach_diagnostics(exc, trace, durations, errors, {'embedding': int('embedding' in durations)})
        raise
    result.stage_trace = [*trace, *result.stage_trace]
    result.signal_durations_ms = {**durations, **result.signal_durations_ms}
    result.signal_errors = {**errors, **result.signal_errors}
    result.model_calls['embedding'] = int('embedding' in durations)
    return complete_result(result)
