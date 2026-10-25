---
name: summarization
display_name: 文本归纳总结
category: task
allowed_agents: [CareerAgent, CampusAgent, LearningAgent]
intents: []
keywords: [总结, 归纳, 概括, 提炼, 整理重点]
priority: 40
enabled: true
---

# 目标
把已经获得的内容整理成简洁、层次清晰的聊天文本。

# 适用场景
用户明确要求总结、归纳、概括或提炼重点。

# 执行步骤
1. 先给一句核心结论。
2. 再列出三到七个关键点。
3. 需要时补充行动项或待确认问题。
4. 保留原有引用，不改变事实含义。

# 输出要求
仅输出聊天文本；不生成 Markdown 文件、DOCX、PDF 或 PPTX。

# 禁止事项
不得在总结时补充原文中不存在的事实。

