---
name: citation-grounding
display_name: 引用与证据约束
category: policy
allowed_agents: [CareerAgent, CampusAgent, LearningAgent]
intents: [career_recruitment, campus_network, campus_affairs, learning_document]
keywords: [来源, 引用, 依据, 规定, 官网, 政策]
priority: 100
enabled: true
mandatory_for_intents: [career_recruitment, campus_network, campus_affairs]
---

# 目标
让涉及学校事实、政策、招聘信息和用户资料的回答能够追溯到真实证据。

# 适用场景
回答学校政策、办事流程、校园网络、近期招聘、用户上传资料，或用户明确要求来源时使用。

# 不适用场景
简单寒暄、纯粹的通用方法建议且不包含可核验事实时不强制使用。

# 所需输入
用户问题，以及工具返回的 Document 证据和 metadata。

# 执行步骤
1. 只陈述证据能够支持的主要事实。
2. 在事实后完整复制工具提供的 `[[e:Agent名:编号]]` 证据标识。
3. 标题、页码、文件名、URL 由程序从实际证据生成，不手写引用元数据。
4. 证据不足或冲突时明确说明，不用模型记忆补齐学校事实。

# 工具使用要求
学校事实必须先调用 Agentic Retrieval 或学校官网查询；用户文件事实必须先调用 Document Reader。

# 输出要求
只输出带证据标识的正文，不列“参考来源”目录、不自行使用数字引用、不输出 HTML。程序统一转换正文编号并展示来源列表。

# 禁止事项
不得伪造页码、URL、发布日期、部门或制度名称。

