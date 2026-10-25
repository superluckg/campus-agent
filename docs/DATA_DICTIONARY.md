# 数据字典

PostgreSQL 使用四个业务 Schema、19 张表。完整字段、类型和关联见 `backend/app/db/models.py`，最终结构初始化入口为 `app.db.initialize`。

| Schema / 表 | 用途 |
|---|---|
| `app.users` | 默认数据归属记录；保留用户编号、用户名、密码哈希、角色和启用状态字段，不提供登录或角色切换 |
| `app.conversations` | 用户归属、标题、命名状态、会话状态和时间 |
| `app.messages` | 会话、请求、Trace、角色、正文、状态、助手和展示元数据 |
| `app.message_citations` | 消息内引用编号、文档/分块、标题、文件、页码、章节、网址、摘录和抓取时间 |
| `app.uploaded_files` | 附件归属、原文件名、上传路径、格式、大小、哈希和解析状态 |
| `app.uploaded_file_contents` | 附件抽取文本、页映射和时间 |
| `knowledge.documents` | 来源、标题、文件名、原始相对路径、网址、既有发布日期、哈希和切分配置 |
| `knowledge.chunks` | 所属文档、原文、1024 维向量、页码、章节、序号和跨页出处 |
| `knowledge.web_sources` | 官网标识、名称、网址、适配器、TTL 和配置 |
| `knowledge.web_snapshots` | 当前成功批次的页面、正文、哈希、抓取和到期时间 |
| `knowledge.ingestion_jobs` | 扫描与入库任务状态、计数、配置和时间 |
| `knowledge.ingestion_items` | 文件路径、标题确认、处理状态、文档和错误 |
| `memory.conversation_summaries` | 每个会话唯一的当前摘要、覆盖消息边界与消息数 |
| `memory.long_term_memories` | 用户、类型、正文、向量、重要性和来源编号 |
| `observability.traces` | 请求来源、输入、意图、路由、助手、Skills、计数、时间与错误 |
| `observability.trace_events` | 有序节点事件、状态、摘要、诊断载荷和耗时 |
| `observability.eval_cases` | 当前用例 UUID、递增 Case 编号、精简用例载荷和内部管理时间 |
| `observability.eval_runs` | 用户、运行状态、重复次数、固定输入、进度、停止请求、汇总和错误 |
| `observability.eval_results` | 运行、Case、重复序号、独立用例内容和评分结果 |

## 资料与引用

每个来源类型及原始相对路径保存一份当前文档。内容哈希和 `metadata_extra.chunking` 决定是否需要替换；更新保持 `document_id`，事务内替换分块。`(document_id, chunk_index)` 保证分块序号唯一。

`relative_path` 是 raw 下的实际相对路径。`published_at` 用于来源说明，不作日期硬过滤。分块的 `page_number` 是起始页，`metadata_extra.page_numbers/page_spans` 保存全部物理页和字符区间。

引用编号只表示单次回答中的来源顺序。消息 `display_metadata.render_citations` 控制正文引用格式化，`citation_page_numbers` 保存页码。引用摘录与页码随消息持久化，资料更新不会重新计算已保存回答的引用展示。

## 会话与记忆

标题状态为 `pending`、`generated`、`custom`。普通会话从待命名开始，首条提问生成临时标题，主题命名成功后固定；自定义名称保持不变。`last_user_message_at` 由用户消息聚合，用于列表排序。

消息的 `request_id` 与 `trace_id` 同时为空或同时有值，组合外键保证请求、Trace 和会话一致。删除会话级联清理消息、引用、摘要、附件登记和抽取文本；长期记忆与 Trace 保留来源编号。

每个会话只保存一条当前摘要。近期消息缓存丢失时由 PostgreSQL 恢复。长期记忆使用默认用户编号关联，使用 pgvector 召回。已有其他编号名下的数据不会自动合并或删除。

## 评测与 Trace

Case 编号由 `observability.eval_case_number_seq` 递增分配，允许空洞且不复用。运行固定用例、配置和官网成功缓存，结果保存独立用例内容，移除当前 Case 不级联删除结果。`ix_eval_one_active` 限制同时最多一个 queued/running 运行。

Trace 的 `request_source` 区分 chat 和 evaluation，`query_snapshot` 保存请求输入。阶段载荷通过 `span_id/parent_span_id` 表达嵌套和并行，时间表示真实经过时长，实际检索批次按开始事件计数。完整上下文及工具产物仅对开发者可见。

数据库时刻使用带时区 UTC，页面、导出和运行日志显示 UTC+8。数据库页面只读固定业务表，支持分页、搜索、精确筛选和关联跳转，敏感字段隐藏。

## Redis

| Key | TTL | 内容 |
|---|---|---|
| `recent_messages:<conversation_id>` | 24 小时 | 最近八条消息的可恢复镜像 |
| `lock:conversation:<conversation_id>` | 300 秒 | 会话互斥令牌 |
| `sse:{<request_id>}:events` | 1 小时 | Redis Streams 可重放事件 |
| `sse:{<request_id>}:counter` | 1 小时 | 请求内事件编号 |
| `sse:{<request_id>}:terminal` | 1 小时 | 终止事件编号 |

Redis 允许丢失，不作为不可恢复业务数据的唯一来源。官网每个来源仅保留最近成功抓取的完整批次，失败不会覆盖它。用例字段见下文，执行与评分见[代码字典](CODE_DICTIONARY.md#统一评测的执行与评分)。

## 评测用例载荷

`observability.eval_cases.payload` 与交付 JSONL 使用同一精简结构，`data/evaluation/cases.jsonl` 每行一个完整对象。Case编号及UUID由系统生成，不写入JSONL；数据库内部创建与更新时间用于记录管理，不要求人工填写，Case接口不返回更新时间。

| 必填字段 | 含义与约束 |
|---|---|
| `query` | 输入问题，非空，最多4000字 |
| `expected_intent` | 主意图；取值见下表 |
| `expected_behavior` | 预期回答行为；取值见下表 |
| `reference_answer` | 规范回答，保留必要条件、例外及适用范围，接受等价表达；非空，最多30000字 |
| `evidence` | 标准原文证据数组，最多50条；无标准来源时为`[]` |

| 字段 | 允许值 |
|---|---|
| `expected_intent` | `career_recruitment`招聘就业、`campus_network`校园网、`campus_affairs`校园事务、`learning_plan`学习规划、`compound`复合、`unclear`不清楚、`small_talk`闲聊、`out_of_scope`范围外 |
| `expected_behavior` | `answer`回答、`partial_answer`部分回答、`clarify`追问、`refuse`权限拒答、`explain_limitation`说明范围或知识限制、`small_talk`闲聊 |

`refuse`行为推导为拒绝策略、直接回复且无需助手；其他行为为允许策略，再由主意图确定单助手、多助手、追问或直接回复。普通范围外查询使用`explain_limitation`，与权限拒答区分。路由、助手和策略由程序生成，不作为人工重复维护的字段。

### 标准原文证据

PDF证据只需`filename`、`page_number`、`content`；页码使用从1开始的PDF物理页码。

| 证据字段 | 填写要求 |
|---|---|
| `filename` | 本地知识库真实文件名，本地证据必填 |
| `page_number` | 物理页码，最小为1；无页码的资料可省略 |
| `content` | 连续原文，不改写或拼接不相邻段落；至少4个有效文字字符，最多30000字 |
| `section_path` | 按需填写的章节线索，供溯源展示 |
| `source_kind` | 默认`knowledge_file`；官网证据填写`official_web` |
| `source_key` | 官网注册来源标识，官网证据必填 |
| `source_url` | 官网原文网址，不用于抓取任意地址 |
| `related_intents` | 复合任务必填，关联一个或两个已选子意图；单任务由程序确定 |

表单预览只读已入库文档和注册来源的成功缓存，不读取任意磁盘路径、不实时抓取。相邻chunk的重叠去重，跨页原文建议分段摘取；原文和页码会参与运行前定位。修改复合子意图时保留证据原文，失效关联须重新选择。保存不执行完整定位；启动前若资料缺失、页码不符或原文不匹配，则阻止运行。参考回答不用于搜索标准chunk。

### 按场景使用的条件字段

| 字段 | 使用方式 |
|---|---|
| `conversation_context` | 固定历史消息，按发生顺序填写`{"role":"user","content":"消息"}`；角色为`user`或`assistant`，最多8条，每条非空且最多12000字 |
| `expected_sub_intents` | 仅复合任务填写两个不同助手领域的子意图；校园网和校园事务均属校园助手，不能构成双助手任务 |

不使用的条件字段省略，不携带空占位。未知字段会拒绝；导入不接受嵌套的输入、人工路由或回答标准对象。运行与结果使用独立输入快照，运行开始结束时间和耗时继续用于状态展示。

## 交付数据

| 路径 | 内容 |
|---|---|
| `data/raw.zip` | 13份资料：10份PDF、1份DOCX、2份TXT，涵盖学生与研究生手册、学籍学位、教学管理和校园服务；文件位于压缩包根部，中文名称为UTF-8 |
| `data/knowledge_base/raw/` | 解压后的原始资料目录，受Git忽略；解析、分块和向量在工作台确认入库后生成，不改写原文 |
| `data/evaluation/cases.jsonl` | 5个手工审核用例：转专业、社团借教室、英语免修、未经授权获取非公开经费、范围外演唱会查询；供本地开发和手动导入使用 |

原始材料的发布机构、文号和发布日期以文件正文为准，涉及政策时应核对原文及学校最新通知。项目MIT许可证不覆盖学校原文和用户上传资料，其权利归相应权利人所有。

当前资料包SHA-256：

```text
d1822d76ea7a7839888670815bc9edad7e5a28def77d513febbb6fb7adc9c94f
```
