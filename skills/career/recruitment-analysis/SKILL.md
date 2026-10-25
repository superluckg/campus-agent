---
name: recruitment-analysis
display_name: 招聘信息分析
category: domain
allowed_agents: [CareerAgent]
intents: [career_recruitment]
keywords: [招聘会, 双选会, 校招, 就业, 招聘通知]
priority: 70
enabled: true
---

# 目标
从预先登记的学校官网和公共知识库中提取近期招聘与就业服务信息。

# 适用场景
询问近期招聘会、双选会、就业活动或学校就业服务。

# 执行步骤
1. 近期信息优先使用学校官网查询。
2. 政策和方法类内容可以使用 Agentic Retrieval。
3. 提取活动名称、时间、地点、适用对象和注意事项。
4. 无发布日期或信息可能过期时明确标注。

# 输出要求
按照“招聘信息、时间与地点、适用对象、注意事项”组织；事实后复制本轮证据标识，不另列参考来源目录。

# 禁止事项
不得访问注册表之外的 URL，不得把旧快照描述为实时信息。

