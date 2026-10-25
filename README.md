# QUST Campus Agent｜青岛科技大学校园生活助手

面向青岛科技大学在校生，搭建一站式咨询平台。学生在遇到问题时，不用纠结“找谁问？”“去哪查？”直接向助手提问，可以得到基于本校资料、来源可追溯的解释和指引，减少找人打听、搜索资料、翻阅文件等负担，降低沟通与时间成本。

内置开发者工作台，支持Trace与分层评测，用于分析耗时、定位问题和验证优化效果。

[快速开始](#快速开始) · [资料与用例导入](#资料与用例导入) · [核心能力](#核心能力) · [架构与技术栈](#架构与技术栈) · [技术文档](#技术文档)

## 核心能力

| 能力 | 描述 |
|---|---|
| 校园、求职与学习助手 | 根据问题选择校园、就业或学习助手；复合任务支持双助手协作 |
| 混合检索与来源引用 | BM25 与 BGE-M3 向量召回经 RRF 融合，结合已注册的官网来源，回答附带出处与页码 |
| 多轮对话与资料阅读 | 流式回答、会话恢复、摘要与长期记忆，支持上传资料供当前会话使用 |
| 按任务选择 Skills | 为业务助手选择对应的提示与工具，支持资料检索、文件阅读和 GPA 计算 |
| 开发者工作台 | 查看执行 Trace、阶段耗时及工具调用，管理知识库入库和意图、检索、生成分层评测 |

## 架构与技术栈

前后端在本机分别启动；Docker Compose 管理 PostgreSQL、Redis、Ollama 三个基础服务。

```text
React + TypeScript（localhost:3000）
              │ HTTP / SSE
FastAPI + LangGraph（127.0.0.1:8000）
              │
     Docker Compose 基础服务
     ├─ PostgreSQL / pgvector：业务数据与向量
     ├─ Redis：近期消息缓存与流事件
     └─ Ollama / BGE-M3：文本向量化
```

| 组件 | 技术 |
|---|---|
| 前端 | React、TypeScript、Vite、Ant Design |
| 后端与 Agent | Python、FastAPI、LangChain、LangGraph |
| 意图识别 | 精确规则、BGE-M3 向量、Typesafe Jev、DeepSeek 四级漏斗 |
| 检索与数据 | BM25、RRF、PostgreSQL、pgvector、SQLAlchemy |
| 模型服务 | DeepSeek API；Ollama 提供本地 BGE-M3 向量模型 |
| 消息与部署 | Redis、SSE、Docker Compose |

## 快速开始

| 配置项 | 要求／入口 |
|---|---|
| Python | Python 3.11，使用 Conda 或 venv 管理独立环境 |
| Node.js | Node.js 22，至少22.18，包含 npm |
| 基础服务 | Docker Desktop，使用 WSL 2 后端与 Linux 容器 |
| GPU | 当前 Ollama 容器配置需要支持 Docker / WSL 2 GPU 的 NVIDIA 环境 |
| DeepSeek API | [获取 API Key](https://platform.deepseek.com/api_keys)，填写 `DEEPSEEK_API_KEY` |
| Typesafe API | [API 控制台](https://console.typesafe.ai/)，填写 `TYPESAFE_API_KEY` |

### 首次启动

打开一个终端

#### 1. 下载项目

```powershell
git clone https://github.com/superluckg/qust-campus-agent.git
cd qust-campus-agent
```

#### 2. 创建并进入 Python 环境

以下两种方式任选一种，后续依赖安装与启动步骤相同。

**使用 Conda：**

```powershell
conda create -n qust-campus-agent python=3.11 -y
conda activate qust-campus-agent
```

**没有 Conda：使用 Python 自带的 venv。**

先从 [Python 官网](https://www.python.org/downloads/windows/)安装 Python 3.11（包含 Windows 启动器 `py`），再在项目根目录执行：

```powershell
py -3.11 -m venv ..\qust-campus-agent-venv
..\qust-campus-agent-venv\Scripts\Activate.ps1
```

venv 环境保存在项目同级的 `qust-campus-agent-venv/` 中。

#### 3. 安装后端依赖与浏览器

```powershell
cd backend
python -m pip install .
python -m playwright install chromium
cd ..
```

后端依赖声明在 `backend/pyproject.toml`；Chromium 是动态官网抓取使用的浏览器运行程序。

#### 4. 安装前端依赖

```powershell
cd frontend
npm.cmd ci
cd ..
```

#### 5. 配置环境变量

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad.exe .env
```

填写自己的 `DEEPSEEK_API_KEY` 和 `TYPESAFE_API_KEY` 并保存，其余配置可沿用模板。

#### 6. 启动基础服务并下载向量模型

启动 Docker Desktop，然后执行：

```powershell
docker compose pull --policy always postgres redis ollama
docker compose up -d --wait postgres redis ollama
docker exec qust-ollama ollama pull bge-m3:latest
```

三条命令依次完成：下载基础服务镜像、启动并等待服务就绪、下载 BGE-M3 向量模型。

#### 7. 启动后端

```powershell
.\scripts\dev-backend.ps1 -PythonPath (Get-Command python.exe).Source
```

脚本读取 `.env`，自动建立空库所需的表和索引或核验已有结构，然后启动后端；保持此终端运行。

#### 8. 启动前端（再打开一个终端）

进入同一项目根目录后执行：

```powershell
.\scripts\dev-frontend.ps1
```

打开 [http://localhost:3000](http://localhost:3000)，直接进入开发者工作台。  
资料与用例的准备见[资料与用例导入](#资料与用例导入)。

### 再次启动

依赖、模型和配置未变化时，直接按以下步骤启动。

#### 1. 启动基础服务

启动 Docker Desktop，在项目根目录执行：

```powershell
docker compose up -d --wait postgres redis ollama
```

#### 2. 启动后端（终端一）

先按首次启动时选择的方式激活环境。

Conda 环境：

```powershell
conda activate qust-campus-agent
```

venv 环境：

```powershell
..\qust-campus-agent-venv\Scripts\Activate.ps1
```

然后启动后端：

```powershell
.\scripts\dev-backend.ps1 -PythonPath (Get-Command python.exe).Source
```

#### 3. 启动前端（终端二）

另开 PowerShell，进入同一项目根目录，执行：

```powershell
.\scripts\dev-frontend.ps1
```

再次打开 [http://localhost:3000](http://localhost:3000) 即可使用；  
已有资料和用例无需重复导入；  
前后端终端按 `Ctrl+C` 停止；  
`docker compose stop` 停止基础服务并保留数据库和模型数据卷。

## 资料与用例导入

### 导入知识库资料

1. 在项目根目录解压提供的资料包：

   ```powershell
   Expand-Archive .\data\raw.zip .\data\knowledge_base\raw
   ```

2. 打开开发者工作台，进入“知识库入库”，点击“扫描raw目录”。
3. 核对13份文件及标题，点击“确认入库”。
4. 等待全部文件入库完成，再进入对话页提问。

### 导入评测用例

1. 打开 [cases.jsonl](data/evaluation/cases.jsonl)，复制全部文件内容。
2. 在开发者工作台进入“评测 → 评测集管理”，点击“批量导入”。
3. 粘贴复制的 JSONL 内容并确认。
4. 在用例列表核对导入的5条示例；正式评测从“评测运行”中发起。

用例导入采用追加方式，已有这5条示例时无需重复导入。

## 技术文档

| 文档 | 内容 |
|---|---|
| [代码字典](docs/CODE_DICTIONARY.md) | 模块职责、执行与评分逻辑、开发验证 |
| [数据字典](docs/DATA_DICTIONARY.md) | 数据表、关联关系、资料与评测字段 |
| [API 文档](docs/API.md) | 聊天、Trace、入库、评测及只读数据库接口 |

## 项目结构
```text
backend/    后端源码与正式测试
frontend/   前端页面与测试
config/     应用、模型、意图及官网来源配置
skills/     业务与策略 Skill
data/       资料包与评测示例
runtime/    会话上传副本
scripts/    本地前后端启动脚本
docs/       三份技术文档
```

## 许可证

项目代码及作者编写的文档采用 [MIT License](LICENSE)，Copyright © 2026 [superluckg](https://github.com/superluckg)。允许使用、修改和二次开发，须保留版权与许可证声明。

学校原文和用户上传资料的权利归相应权利人所有，MIT 许可证不覆盖这些内容；第三方组件遵循各自的许可证。
