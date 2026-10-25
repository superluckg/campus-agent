---
name: study-planning
display_name: 学习计划
category: domain
allowed_agents: [LearningAgent]
intents: [learning_plan]
keywords: [学习计划, 复习计划, 备考, 时间安排, 学习目标]
priority: 70
enabled: true
---

# 目标
根据目标、期限、可投入时间和已有资料制定可执行的学习计划。

# 适用场景
课程复习、阶段学习、备考和资料使用安排。

# 所需输入
学习目标、截止时间、可投入时间；缺少关键字段时先反问。

# 执行步骤
1. 明确目标和约束。
2. 拆分阶段、任务和检查点。
3. 为每阶段设置可验证的产出。
4. 预留复盘和调整时间。

# 输出要求
计划保持简洁，使用阶段、任务、时间和验收标准组织。

# 禁止事项
不得声称读取了真实课表或教务数据，除非用户已经明确提供相应资料。

