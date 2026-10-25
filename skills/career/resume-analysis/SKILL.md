---
name: resume-analysis
display_name: 简历分析
category: domain
allowed_agents: [CareerAgent]
intents: [career_resume]
keywords: [简历, 经历, 项目经历, 求职材料, 岗位匹配]
priority: 70
enabled: true
---

# 目标
基于用户上传的真实简历给出可执行的修改建议和局部改写示例。

# 适用场景
简历诊断、岗位匹配、项目经历表达和简历结构优化。

# 所需输入
简历文件；如需岗位匹配，还需要目标岗位或岗位描述。

# 执行步骤
1. 使用 Document Reader 阅读用户明确选择的文件。
2. 检查结构、信息完整性、成果量化、岗位相关性和表达清晰度。
3. 按影响程度排列问题。
4. 给出局部改写示例，但不虚构经历和数据。

# 输出要求
默认按照“总体判断、主要问题、修改建议、局部改写示例”组织，不输出新 PDF、DOCX 或 TXT 文件。

# 禁止事项
不提供缺乏客观依据的数字评分，不改写成虚假的教育、项目或实习经历。

