# API 摘要

Swagger、ReDoc、OpenAPI JSON及Swagger回调入口均关闭；接口以本文和源码为准。

所有 `/api` 业务接口无需登录，仅接受 local/development 环境的本机请求，并检查 Host、Origin 和代理转发来源。后端统一使用默认数据归属编号，不接受浏览器选择身份。会话、附件、记忆和事件请求继续校验其数据归属，同一部署共享工作台数据。

## 统一评测

前缀`/api/developer/evaluation`。字段、允许值和证据定位规则见[数据字典](DATA_DICTIONARY.md#评测用例载荷)，指标及执行隔离见[代码字典](CODE_DICTIONARY.md#统一评测的执行与评分)。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /catalog | 统一用例契约与八类意图 |
| GET | /sources | 有效文档与成功官网缓存列表（只读） |
| GET | /sources/local/{UUID} | 按页与章节重建原文预览，不读取磁盘路径 |
| GET | /sources/web/{source_key} | 注册来源的成功缓存预览，不实时抓取 |
| GET/POST | /cases | 列出当前用例／提交完整Case新增，自动编号 |
| PUT/DELETE | /cases/{UUID} | 原地编辑／永久移除当前记录；结果保留独立快照 |
| POST | /import/preview | `{text}` JSONL全量校验，不写入，错误含行号 |
| POST | /import | `{text}` 原子追加，任何错误整批不写入，返回saved_count与cases |
| POST | /runs | `{case_ids:[UUID,...],repeats:1..5}`预检后异步执行真实共享图；活动运行冲突409 |
| GET | /current | 最新运行、数字进度、汇总及逐条结果；include_results=false时只读进度，不传整个知识库或配置快照 |
| POST | /runs/{UUID}/stop | 立即请求取消，保存已完成和当前部分诊断 |

`current`无运行返回`{run:null,results:[]}`；有运行时run含run_id/status/repeats/progress/summary_metrics/started_at/finished_at/error_message/stop_requested。进度包含total_attempts/completed_attempts/current_case/trial_index/stage。status为queued/running/completed/stopped/failed/interrupted。每条结果含Case独立快照、trial_index、trace_id、prediction、intent_metrics、retrieval、generation、visible_context、duration_ms与错误。

Case编号不可复用、不要求连续；UUID用于关联，不接受人工编号。用例必填`query`、`expected_intent`、`expected_behavior`、`reference_answer`、`evidence`；路由、助手和策略由意图与行为推导，不存为人工字段。Case接口返回`case_id/case_number/case_label/payload`，不返回更新时间。历史消息按需填写，最多八条。启动前证据不定位则422且不建运行，明确不可回答允许无证据。运行中编辑或删除Case不改变当次快照。官网固定缓存、本地指纹变化中止；不写真实聊天或长期记忆。Skills正常执行，不评分。结果只展示最新一次。

### 创建、编辑与批量导入

POST `/cases` 或 PUT `/cases/{UUID}` 提交精简用例，例如：

```json
{
  "query": "你好！",
  "expected_intent": "small_talk",
  "expected_behavior": "small_talk",
  "reference_answer": "你好！有什么需要帮助的？",
  "evidence": []
}
```

创建与编辑返回 `{saved_count,cases}`，其中每条Case为 `{case_id,case_number,case_label,payload}`；输入校验失败返回422中文错误。删除成功返回 `{deleted:true}`，不存在返回404。

批量导入请求为 `{text:"每行一个完整JSON对象的JSONL文本"}`。预览返回 `{valid_count,errors:[{line,message}]}`；正式导入有错误则422，detail包含 `{message,errors}`，整批不写入；成功返回201及 `{saved_count,cases}`。导入只追加，同一示例重复导入会新增相同内容的Case。

运行请求为 `{case_ids:[UUID,...],repeats:1..5}`，不接受重复ID。来源无法定位时返回422且不创建运行；用例不存在返回404；已有活动运行或预检期间用例改变返回409。运行结果区分 `not_applicable`、`upstream_blocked`、`execution_failed`、`judge_failed` 与 `cancelled`；未评分的指标保留null或未提供值，不能视为0分或满分。

## Trace与入库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/developer/traces | 默认仅聊天Trace，含query输入摘要与route实际路由 |
| GET | /api/developer/traces/{UUID} | `{trace,events,stages}`完整详情；阶段包含父子跨度、真实时间、输出与错误 |
| GET | /api/developer/traces/{UUID}/events | 按顺序读取数据库事件 |
| GET | /api/developer/skills | 受保护的当前 Skills 热加载清单 |
| POST | /api/developer/ingestion/scan | 扫描 raw 文件并生成待确认标题 |
| GET | /api/developer/ingestion/current | 当前或最近的入库任务，无任务返回null |
| GET | /api/developer/ingestion/{UUID} | 入库任务进度 |
| POST | /api/developer/ingestion/{UUID}/confirm | 确认待处理项和标题，拒绝未定义字段 |
| POST | /api/developer/ingestion/{UUID}/start | 启动已确认任务并刷新索引 |

完整输入、阶段产物和context.injected实际注入块写入请求Trace；工作台SSE按事件类型提供执行进度与回答正文。未采集的信息显示未记录。phase.started/completed/failed/cancelled通过span_id归并，parent_span_id描述嵌套／并行。总耗时为实际经过时间，后台维护独立列出。Trace显示日期与时间间两个空格和UTC+8；原始JSON保留存储值。

## 聊天与只读数据库

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | /api/conversations | 列出／创建自己的聊天 |
| DELETE | /api/conversations/{UUID} | 永久移除自己的聊天和上传副本，保留长期记忆和Trace |
| GET/POST | /api/conversations/{UUID}/messages | 读取消息／提交用户问题 |
| GET | /api/conversations/{UUID}/requests/{request_id}/events | 本机 SSE 事件订阅，支持after重放 |
| GET/POST | /api/conversations/{UUID}/files | 列出／上传自己的附件；生产聊天附件功能保留 |
| GET/PATCH/DELETE | /api/memories[/{UUID}] | 查看／修改／移除自己的长期记忆 |
| GET | /api/developer/database[/{schema}/{table}] | 固定业务表目录／只读分页查询 |
| GET | /api/developer/database/{schema}/{table}/records/{key} | 完整只读记录 |
| GET | /health/live与/health/ready | 存活／依赖就绪 |

### 最近聊天、命名与删除

会话响应包含 `last_user_message_at`（最新已保存 user 消息时间；空会话为 null）、`title_status`（pending/generated/custom）、`is_busy`。列表按最新 user 消息时间降序；空会话按创建时间排序，assistant 消息和后台维护不影响排序。

默认标题为“新对话”的新会话进入 pending：首条用户消息的前 24 字作为临时标题；成功回答之后独立调用现有 memory_model 概括明确主题，生成后固定名称。纯寒暄继续 pending，命名失败保留临时名称，下一次成功聊天重试。自定义名称的会话不自动重命名。命名最多等待 30 秒，不延迟最终回答或改变其状态。

DELETE 成功返回 `{deleted:true, conversation_id:UUID}`。不存在或不属于当前用户返回 404；排队、生成、上传、记忆维护或命名期间返回 409；上传路径不安全或文件清理失败返回 503。数据库级联删除消息、引用、摘要、上传登记和抽取文本，清理该 UUID 上传目录中的副本及近期消息/SSE 缓存；保留公共知识资料、长期记忆及 Trace，其来源编号继续留存。文件清理或数据库提交失败会尝试恢复文件并回滚数据库。当前单 worker 部署使用进程内活动登记；扩大为多 worker 前须改为共享活动锁。

数据库及 API 时间继续使用带时区的 UTC 值。前端展示、结果导出和运行日志固定北京时间 UTC+8，聊天列表显示 `YYYY/MM/DD · HH:mm`，悬停可看秒数；空聊天显示“尚未开始”。

数据库分页参数：`page`（默认 1）、`page_size`（默认 20，最多 100）、`q`（文本搜索）、`filters`（JSON 对象，如 `{"conversation_id":"UUID"}`）、`sort_by`（字段名）、`descending`（默认 true）。筛选值使用文本表示；`"__NULL__"` 表示筛选数据库 NULL。字段筛选使用精确匹配，多个条件共同生效。数据库接口仅接受固定业务表和可用字段，所有数据查询均参数化；不提供任意 SQL 或写入操作。



## 意图诊断与评分

意图识别策略为 funnel。intent.completed及评测prediction保存decision_stage/stage_trace/model_calls/signal_durations_ms/signal_errors/decision_details。规则、向量、Jev和DeepSeek分层展示；信号降级与业务失败区分。子意图评分比较识别出的原子任务，即使后续追问仍保留。

summary_metrics按intent/retrieval/generation分组。每次真实检索最多5条，排名只展示@5；证据取得使用70%文字覆盖。先在Case内汇总，再按Case等权；不适用、上游阻断、执行失败和评分失败分别保存。忠实度只看实际可见内容；拒答质量仅预期refuse，否则null。无整体通过判定，抽屉不展示公式或分母。

## 正文流式事件与恢复

先 POST 消息取得 `request_id`，再通过 fetch GET 订阅对应事件接口，无需附加身份凭据。每个已保存事件都有请求内递增的整数 `id`；`after=N` 只读取编号大于 N 的事件。工作台订阅包含对应请求的 Trace 与正文事件，前端按事件类型更新进度和回答。

| 事件 | data 字段 | 前端处理 |
|---|---|---|
| `response.started` | `response_id` | 初始化回答气泡；等于当前 request_id |
| `response.delta` | `response_id`、`delta`、`server_emitted_at` | 追加正文片段 |
| `response.replace` | `response_id`、`content`、`server_emitted_at` | 结构化纠正或合并降级时替换预览 |
| `response.completed` | `response_id`、`message_id`、`content`、`agents`、`citations`、`partial`、`unresolved_points`、`status` | 保存成功后的完整结果；替换临时消息、展示引用并结束订阅 |
| `request.failed` | `response_id`、`code`、`message` | 保留草稿并标记未完成；不保存 assistant 草稿、不写入记忆 |

单 Agent 从内部 `AgentAnswer.answer` 提取正文，双 Agent 从 `ComposedAnswer.content` 提取合并正文；对外仍为 `content + citations`。内部证据标识先转换再发送，未闭合标识缓冲。`response.delta`、`response.replace` 和 `response.completed` 可携带 `display_metadata` 与当前 `citations`；完成事件及历史接口共同保留 `render_citations=true`、引用核验提示及实际页码快照。前端对启用格式化的有效正文引用渲染上标 `[n]`。工具参数、内部模型文字和检索规划不会进入正文。

正文首片立即发送，后续按 50 毫秒或 256 字符合并。Redis 使用独立的 `sse:{request_id}:*` Streams 命名空间，以 Lua 原子编号、XREAD 按游标读取；默认保留 1 小时。正文分片不逐条写入 PostgreSQL Trace，Trace 保存阶段、错误、完成和 `response.stream` 汇总指标。空闲连接每 15 秒发送 SSE 注释心跳。

前端按事件编号去重，断线后按 1、2、4 秒最多自动续接三次，始终 GET 同一个请求。请求、游标和草稿作为一个快照写入 sessionStorage；刷新后恢复。切换会话或离开页面会关闭订阅，后台请求继续执行。重试耗尽后保留草稿并提供“重新连接”。

事件不存在返回 HTTP 410（`STREAM_EXPIRED`）；游标已越过终止事件返回 HTTP 410（`REQUEST_SETTLED`）。连接中途过期会发出无 id 的 `stream.expired` 控制事件。客户端遇到这些情况应 GET 消息列表，用该 request_id 的已保存 assistant 消息完成恢复；没有已保存结果则标记未完成并结束恢复，不无限等待。
