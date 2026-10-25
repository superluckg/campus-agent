# 代码字典

下表列出当前实现的入口和职责，完整参数以源码与 [API 文档](API.md)为准。

| 模块或对象 | 职责 |
|---|---|
| `app.main` | FastAPI 入口、生命周期、CORS 和路由注册 |
| `AppContainer` / `build_container()` | 装配数据连接、模型、仓库、检索器、助手和全局图 |
| `app.db.initialize` | 创建空库最终结构，核验已有库，建立向量索引、单运行约束和编号序列 |
| `GlobalState` / `build_global_graph()` | 上下文、意图、路由、记忆、Skills、助手与合并的状态和流程 |
| `ChatRunService` | 请求锁、后台执行、SSE、消息保存、Trace 和记忆维护 |
| `IntentRecognitionService` / `intent.funnel` | 规则、向量、Jev、DeepSeek 四级漏斗 |
| `JevDecisionClient` | 决策调用、受约束结果解析和供应商错误处理 |
| `AgentRouter` / `DirectReplyService` | 确定性路由和无工具的追问、闲聊、范围说明、拒绝 |
| `AgentFactory` / `AgentRunner` | 创建和执行 LangChain 业务助手循环及流式正文 |
| `PromptAssembler` / `ResponseComposer` | 动态提示组装及单助手交付、双助手合并 |
| `EvidenceRegistry` / `normalize_answer()` | 真实证据标识、正文引用转换和来源快照 |
| `SkillDefinition` / `SkillManager` | 字段校验、权限筛选、候选打分、提示预算与热加载 |
| `LocalRetrievalService` / `BM25Index` | 词法与向量召回、输入去重和成功查询缓存 |
| `reciprocal_rank_fusion()` | 按排名融合、候选去重和稳定排序 |
| `PlannedKnowledgeRetriever` / `RetrievalSession` | 执行助手指定计划、来源校验、批次预算和失败隔离 |
| `QueryPlan` / `RetrievalExecution` | 查询策略、来源、结果和诊断 |
| `WebSourceRegistry` / `WebSourceSelector` | 固定官网配置与权限、来源选择 |
| `WebSourceRetrievalService` | 并发抓取、成功缓存和单源失败降级 |
| `StaticHtmlAdapter` / `DynamicListAdapter` | 静态正文与动态列表首页解析 |
| `DocumentReaderService` / `UploadService` | 授权文件分页续读、输出限制、上传校验与文本抽取 |
| `ScopedUploadResolver` | 文件归属与目录越界检查 |
| `GPACalculatorService` | XLSX 课程读取、Decimal 加权和确定性绩点换算 |
| `KnowledgeIngestionService` / `split_sections()` | 扫描、确认、解析、自然边界分块、Embedding 与当前文档替换 |
| `core.source_pages` | 跨页字符映射、页码显示与原文投影 |
| `MemoryService` / `PostgresContextLoader` | 近期对话、当前摘要和长期记忆维护 |
| `RedisEventBus` | Lua 原子编号、Streams 重放和游标订阅 |
| `ConversationManagementService` | 会话活动互斥、命名和所属文件的事务删除 |
| `observation_scope()` / `span()` / `observed_tools()` | 真实计时、父子跨度、注入上下文和工具产物采集 |
| `TraceRepository` | 请求记录、阶段事件、实际检索批次和详情 |
| `UnifiedCase` / `decision_reference()` | 校验精简人工字段，确定性推导策略、路由和助手 |
| `UnifiedEvaluationRepository` | 原子导入、递增编号、单运行约束和独立结果记录 |
| `FixedInputs` / `UnifiedEvaluationService` | 固定评测上下文、共享业务图执行、停止和恢复 |
| `OriginalEvidenceMatcher` / `prepare_case()` | 原文定位与相关证据集构建 |
| `unified_scoring` / `UnifiedJudge` | 意图评分、每调用 @5、Case 等权汇总和生成六维评分 |
| `core.errors` | 供应商停止、额度耗尽和评测冲突的共享错误语义 |

## 数据访问与前端

`app/db/repositories/` 按工作台默认归属、会话、附件、知识、官网、记忆、Trace、入库、评测和只读浏览分工，`Database.session()` 统一管理事务和连接。`UserRepository.ensure_workspace_user()` 复用默认记录编号，`workspace_user` 为本机 API 提供该编号，不进行密码或角色验证。

`PostgresKnowledgeRepository.replace_document()` 对同一路径加事务锁，未变则复用，变化时保持文档编号并替换分块。`MemoryRepository.add_summary()` 覆盖唯一的当前摘要，`WebCacheRepository.save()` 原子替换官网来源的完整成功批次。

前端入口为 `src/main.tsx` 和 `App.tsx`，页面位于 `src/pages/`。`MessageBubble` 显示正文及引用，`chatStream.ts` 管理事件、草稿和续接。`UnifiedEvaluationPanel` 整合用例、运行和结果；`TraceDrawer` 与 `traceDetails.ts` 展示真实阶段及父子检索批次。`utils/evaluationLabels.ts` 提供当前路由、阶段和指标说明，`utils/time.ts` 统一时间显示和导出。

## 配置与验证

`config/` 保存应用、模型、意图和官网配置；控制台日志的北京时间与敏感信息处理位于 `app/core/logging.py`。`skills/` 保存九份业务及策略提示。正式测试位于后端和前端的 `tests/`。数据库验收使用独立创建的临时数据库。

## 统一评测的执行与评分

`FixedInputs` 只向被测助手提供问题和最多8条固定历史消息，不加载真实聊天、会话摘要、长期记忆或附件；参考回答与标准原文仅用于预检和评分。正常聊天保留完整业务能力。

`prepare_case()` 使用 `OriginalEvidenceMatcher` 在完整固定资料中定位标准原文，不使用参考回答搜索标准chunk，也不根据被测检索结果反推标准集合。官网使用运行开始时已有的成功缓存；本地索引指纹变化时停止运行，避免混用资料。

| 指标 | 计算与适用方式 |
|---|---|
| 意图与决策 | 比较主意图、路由、助手集合、子意图和处理策略；整体决策及重复一致率使用这五项。实际拒绝原因保留在预测和Trace中，评分时统一到拒绝策略 |
| Hit@5 | 每次实际检索的前5条是否命中标准相关chunk |
| Precision@5 | 前5条中命中的不同标准chunk数除以5 |
| 片段召回率@5 | 命中的不同标准chunk数除以全部标准相关chunk数；多个证据段指向同一chunk时去重 |
| MRR@5 | 首个相关结果排名的倒数；未命中为0 |
| 证据项召回率@5 | 标准证据中被取得的比例；文本覆盖达到70%视为取得 |
| 证据文本覆盖率@5 | 标准原文被返回证据覆盖的文字比例 |
| 生成质量 | 正确性、忠实度、相关性、完整性、实用性、拒答质量，每项0～5分 |

每个检索调用独立计算，再汇总用例内调用与重复，最后对用例等权汇总。无标准相关集、没有实际检索或检索失败均不伪填0或满分，分别记录不适用或失败原因。

`UnifiedJudge` 的正确性、完整性对照问题、参考回答和标准原文；忠实度仅依据被测助手实际可见的历史与工具输出，没有事实依据时不评分。拒答质量只适用于 `expected_behavior=refuse`，普通范围外查询不适用。

## 开发验证

首次需要测试时，在项目Python环境安装开发依赖：`python -m pip install './backend[dev]'`。在项目根目录执行：

```powershell
$testRunRoot = Join-Path ([IO.Path]::GetTempPath()) ('qust-campus-agent-tests-' + [guid]::NewGuid().ToString('N'))
Push-Location .\backend
try {
    python -B -m pytest -p no:cacheprovider --basetemp $testRunRoot
    if ($LASTEXITCODE -ne 0) { throw '后端测试失败' }
}
finally { Pop-Location }
npm.cmd test --prefix .\frontend
```

数据库专项使用 `RUN_UNIFIED_DB_TESTS=1`、`RUN_CONVERSATION_DB_TESTS=1` 显式启用独立临时数据库，Redis专项通过 `STREAM_TEST_REDIS_URL` 指定连接；未启用时按条件跳过。测试使用模拟模型，工作台启动正式评测会调用真实供应商。

需要前端类型检查与静态构建时，在 `frontend/` 执行：

```powershell
.\node_modules\.bin\tsc.cmd -p tsconfig.app.json --noEmit --incremental false
.\node_modules\.bin\tsc.cmd -p tsconfig.node.json --noEmit --composite false --incremental false
.\node_modules\.bin\vite.cmd build --config vite.config.ts
```

静态文件输出到 `frontend/dist/`；日常体验使用开发服务器即可。后端和三个基础服务仍需运行。
