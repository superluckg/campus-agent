---
name: learning-material-analysis
display_name: 学习资料分析
category: domain
allowed_agents: [LearningAgent]
intents: [learning_document]
keywords: [资料, 文档, 讲义, 笔记, 阅读, 解释]
priority: 70
enabled: true
---

# 目标
阅读用户在当前会话中明确选择的资料，并据此解释、归纳或回答问题。

# 适用场景
PDF、DOCX、PPTX、Markdown、TXT 和 Excel 资料问答。

# 执行步骤
1. 文件不明确时先反问。
2. 长文档先读取目录或概览，再读取相关页。
3. 仅根据已读取内容回答文件事实。
4. 没有完整读取全文时说明范围。

# 输出要求
尽可能标注文件名、页码、幻灯片或工作表。

# 禁止事项
不得读取其他会话文件，不支持扫描 PDF、图片或任意 URL。

