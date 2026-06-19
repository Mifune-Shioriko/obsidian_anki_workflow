# Obsidian-Anki AI 整合工作流

基于 Python 和 Google GenAI SDK / 通义千问 (Qwen) 的个人知识管理 (PKM) 自动化工作流。深度整合 Obsidian、Anki 与多智能体系统 (Multi-Agent System)，覆盖从课堂幻灯片自动处理、日常随笔原子化拆分、AI 智能制卡、双向同步、语义关联织网到学术文献检索的一站式学习与复习闭环。

---

## 系统架构总览

```
Obsidian Vault
  Daily Notes  ──>  Atomic Notes  ──>  ## 卡片 (Markdown Table)
       │                 │                      │
       v                 v                      v
  daily_to_atomic   similarity_manager     sync / sync_all
       │                 │                      │
       v                 v                      v
  Gemini / Qwen     Qdrant DB            AnkiConnect :8765
  (LLM API)        (向量检索)             (Anki 本地服务)
       │
       v
   Multi-Agent Router
   @default  @explain  @dig  @grade
   @quiz     @file
```

---

## 核心功能模块

### 1. 双向卡片同步 (`sync.py` / `sync_all.py`)

将 Obsidian 原子笔记中的 `## 卡片` Markdown 表格与 Anki 进行增量双向同步。

- **单笔记同步**：`python sync.py <笔记绝对路径>` — 针对单篇笔记执行精确同步
- **全量批量同步**：`python sync_all.py` — 扫描 `Atomic Notes/` 下所有笔记，批量同步并执行全局孤儿卡片清理
- **媒体无损上传**：自动检测 `[[image.png]]` 双链图片（含缩放参数 `[[image.png|300]]`），通过 AnkiConnect `storeMediaFile` 上传至 Anki 媒体库，并在卡片中替换为 `<img>` 标签
- **LaTeX 防损坏渲染**：采用占位符算法保护公式，防止 python-markdown 引擎破坏 LaTeX 中的下划线和星号。Obsidian 中的 `$...$` / `$$...$$` 自动转换为 Anki MathJax 兼容的 `\(...\)` / `\[...\]`
- **Anki ID 回写**：新增卡片在 Anki 创建后，其 Note ID 自动反向写回 Obsidian 卡片表格第三列
- **全局孤儿清理**：同步结束时自动比对活跃 Obsidian Note ID 集合，删除 Anki 中已失去对应笔记的废弃卡片

### 2. 日记自动原子化拆分 (`daily_to_atomic.py`)

自动将每日随笔日记底部的草稿提取为主题单一的原子笔记。

```bash
python daily_to_atomic.py
```

- 扫描 `Daily Notes/YYYY-MM-DD.md`，以最后一个 `---` 分割线为界提取草稿
- 调用 Gemini 生成适合作为文件名的精炼中文标题（强制 JSON 输出）
- 在 `Atomic Notes/` 下创建带 YAML Frontmatter（日期、标题、UUID `id`）的新笔记
- 在原日记中插入新笔记的 Wiki 链接 `[[标题]]`，并补齐新的分割线
- **防重名机制**：同名文件自动追加递增数字（`标题 1`、`标题 2`）

### 3. 多智能体路由系统 (`router.py` + `agents/`)

在 Obsidian 笔记内部通过 `@agent_name` 指令直接唤醒专业 AI Agent。

```bash
python router.py <笔记路径>
```

#### 路由机制

- **对话历史解析**：用户提问以 Markdown 引用块 `> ` 表示，模型回答在引用块外
- **动态加载**：通过 `importlib` 自动注册 `agents/` 下所有含 `handle()` 函数的模块
- **链式管道**：支持多 Agent 串联调用（如 `@explain @dig @new`），按顺序接力执行，前一个 Agent 的输出自动成为下一个的上下文
- **Agent 会话保持**：只要对话历史中出现过某 Agent，后续无显式指定的提问自动路由给该 Agent
- **格式清洗**：所有 Agent 输出经 `utils.sanitize_format()` 统一处理 — 移除粗体 `**`、规范化列表符号为 `-`、Tab 缩进对齐、中英文间距优化

#### Agent 功能列表

| 命令 | 模块 | 功能 |
|:---|:---|:---|
| `@default` | `default.py` | 基础问答。自动解析 `[[双链]]` 加载关联笔记文本与图片二进制，集成 Google Search |
| `@explain` | `explain.py` | 面向中学生的通俗讲解。加载双链笔记上下文与图片，支持 Google Search |
| `@dig` | `dig.py` | 知识提炼。剥离类比与闲聊，回归严谨学术表述，输出三级标题 + 单知识点 bullet points。屏蔽图片以防过度挖掘细节 |
| `@grade` | `grade.py` | 测验批改。批改用户提交的选择题测验（`- [x]` 标记），给出得分和逐题反馈 |
| `@quiz` | `quiz.py` | 出题生成。根据对话历史出 5 道选择题（含单选和多选），考察知识掌握程度 |
| `@file` | `file.py` | 文档知识库 (NotebookLM 模式)。自动提取对话中的文件路径，上传 PDF/PPTX 至 Gemini，基于文档内容问答。支持缓存与增量更新 |

#### 典型链式管道：幻灯片自动处理

```
@explain → @dig
通俗讲解    知识提炼
```

这是系统最核心的工作流：先让 AI 用通俗语言讲解幻灯片内容，再提炼为严谨的知识点短句。

### 4. 自动幻灯片处理器 (`auto_slide_processor.py`)

将课堂 PDF/PPTX 幻灯片自动转化为原子笔记和 Anki 卡片的全自动流水线。

```bash
python auto_slide_processor.py <幻灯片路径> [-s 起始页] [-e 结束页] [--yes]
```

- **结构预分析**：调用 Gemini 对每页 Slide 进行结构化分类（封面 / 目录 / 过渡页 / 结束页 / 内容页），自动跳过无学术价值的页面
- **交互式确认**：展示分析规划，用户可回车确认、自定义页码或退出
- **全自动流水线**：逐页渲染高清 PNG → 挂载到 Daily Note → 触发 `@explain @dig` 链式管道 → 调用 `daily_to_atomic.py` 打包为原子笔记
- **PPTX 支持**：通过 LibreOffice Headless 自动将 PPTX 转换为 PDF
- **错误回滚**：任何环节失败自动还原 Daily Note 并清理临时文件

### 5. 语义相似度织网引擎 (`similarity_manager.py`)

基于 Qdrant 向量数据库为所有原子笔记构建语义关联网络。

```bash
bash run_similarity.sh
```

- **嵌入模型**：使用 Google `gemini-embedding-2`（3072 维向量）
- **增量缓存**：通过 `.similarity_cache.json` 记录文件 SHA256 哈希，仅对修改/新增的笔记重新计算向量
- **相似链构建**：为每篇笔记检索最多 15 篇余弦相似度 > 0.72 的相关笔记，自动覆写笔记底部的 `## 相关笔记` 区域
- **孤儿清理**：自动检测已被物理删除的笔记，从 Qdrant 中清除对应向量点
- **UUID 自注入**：对缺少 YAML `id` 的笔记自动注入 UUID

### 6. 智能作业求解器 (`solve_hw.py`)

面向数理/工科作业的自动化求解与排版生成流水线。

```bash
python solve_hw.py <作业文件路径>
```

- **多模态题目提取**：通过 Gemini + Pydantic Schema 从 PDF 中提取题目文本、页码及图表归一化坐标
- **高保真图表裁剪**：使用 PyMuPDF 将归一化坐标转换为像素坐标，高清局部裁剪作业插图
- **逐题精确求解**：串行遍历每道题目，仅将对应插图作为视觉输入提交给模型，避免长上下文图表混淆
- **风格压缩**：二次 pass 将解答重构为极简学术递交体（Homework-5 风格），2-8 行内完成
- **Pandoc 编译**：自动修正 LaTeX 格式问题，调用 Pandoc 生成专业排版的 `.tex` 文件

---

## 混合模型路由 (`model_client.py`)

系统支持 Gemini 和通义千问 (Qwen) 双模型服务商，通过环境变量按需路由：

- **统一网关**：`model_client.Client` 根据调用来源（Agent 名称 / 脚本名称）自动选择模型服务商
- **按 Agent 配置**：每个 Agent 和脚本可独立指定使用 `gemini` 或 `qwen`（通过 `AGENT_XXX_PROVIDER` 环境变量）
- **Qwen 兼容层**：完整实现了 Gemini SDK 的 `models`、`files`、`chats` API 到 Qwen (DashScope) OpenAI 兼容接口的适配，包括 Tool Function Calling、Pydantic Schema 结构化输出、多模态图片压缩传输
- **默认推荐**：文本类 Agent（`@default`、`@explain`、`@dig`、`@grade`、`@quiz`）默认使用 Qwen；需要文档/多模态能力的 Agent（`@file`、`solve_hw.py`）默认绑定 Gemini

---

## Anki 卡片模板 (`anki_card_templates/`)

项目提供一套 iOS/Apple 风格的 Anki 卡片模板，包含：

- **Front Template**：问题展示面，内置理解度监控脚本 — 当卡片难度超过阈值时，自动显示"建议复习相关笔记"的提示条，点击通过 Advanced URI 跳转回 Obsidian
- **Back Template**：答案展示面，包含"Open in Obsidian"跳转按钮、深色上下文笔记卡片、Obsidian Callout 自动渲染
- **Styling**：Apple System 字体、学术三线表、紫色主题色 Callout、暗色模式适配

卡片字段定义：

| 字段 | 用途 |
|:---|:---|
| `问题` | 卡片正面内容 |
| `答案` | 卡片背面内容 |
| `Advanced URI` | Obsidian 跳转链接（`obsidian://advanced-uri?vault=...&uid=...`） |
| `原来的笔记` | 卡片所属笔记的完整 HTML 上下文 |

---

## Obsidian 笔记结构规范

每篇原子笔记的标准结构：

```markdown
---
date: 2025-01-15
title: 笔记标题
id: abc123def456
type: from_daily_notes
---

笔记正文内容...

---

> @agent_name 用户提问或指令

AI 回答内容...

## 相关笔记

- [[相关笔记 1]]
- [[相关笔记 2]]

## 卡片

| 问题 | 答案 | Anki ID |
| ---- | ---- | ------- |
| 问题内容 | 答案内容 | 1234567890 |
```

---

## 数学公式格式规范

| 环境 | 行内公式 | 块级公式 |
|:---|:---|:---|
| Obsidian / Markdown 正文 | `$...$` | `$$...$$` |
| Anki 卡片内部 | `\(...\)` | `\[...\]` |

同步引擎会自动将 Obsidian 中的 `$` / `$$` 转换为 Anki 兼容的 `\(...\)` / `\[...\]`。Agent 生成的卡片内容中**禁止出现** `$` 或 `$$`。

---

## 环境配置

### 前置条件

- Python 3.10+
- Anki 客户端（后台运行，安装 AnkiConnect 插件，端口 8765）
- Qdrant 向量数据库（推荐 Docker 部署）
- Obsidian（安装 Advanced URI 插件）

### 外部依赖（可选）

- `pandoc` — 作业求解器 Markdown → LaTeX 转换
- `soffice` (LibreOffice Headless) — PPTX → PDF 转换

### 安装 Python 依赖

```bash
pip install google-genai requests markdown python-dotenv pydantic pymupdf qdrant-client Pillow httpx duckduckgo_search
```

### 环境变量配置

基于 `.env.example` 创建 `.env`：

```ini
# 必填
GOOGLE_API_KEY="your_gemini_api_key"
VAULT_DIR="/path/to/obsidian/vault"

# 混合模型路由 (可选, 默认 qwen)
DEFAULT_LLM_PROVIDER=qwen
QWEN_API_KEY="your_qwen_api_key"
QWEN_MODEL_NAME=qwen3.7-plus

# 各 Agent 模型路由 (可选)
AGENT_DEFAULT_PROVIDER=qwen
AGENT_FILE_PROVIDER=gemini
SCRIPT_SOLVE_HW_PROVIDER=gemini

# Anki 配置 (可选, 有默认值)
ANKI_URL=http://127.0.0.1:8765
ANKI_DECK_NAME=Obsidian
ANKI_NOTE_TYPE=Obsidian
OBSIDIAN_VAULT_NAME=your_vault_name

# Qdrant 配置 (可选)
QDRANT_URL=http://localhost:6333
```

---

## 常用命令速查

| 命令 | 功能 |
|:---|:---|
| `python sync.py <笔记路径>` | 同步单篇笔记到 Anki |
| `python sync_all.py` | 全量同步所有原子笔记 |
| `python daily_to_atomic.py` | 将今日日记草稿拆分为原子笔记 |
| `python router.py <笔记路径>` | 执行多 Agent 路由调用 |
| `bash run_similarity.sh` | 启动 Qdrant 并运行语义关联织网 |
| `python solve_hw.py <文件路径>` | 智能作业求解与 LaTeX 生成 |
| `python auto_slide_processor.py <幻灯片>` | 幻灯片全自动处理流水线 |

---

## 项目结构

```
.
├── agents/                     # Multi-Agent 模块
│   ├── default.py              # 基础问答 Agent
│   ├── explain.py              # 通俗讲解 Agent
│   ├── dig.py                  # 知识提炼 Agent
│   ├── grade.py                # 测验批改 Agent
│   ├── quiz.py                 # 出题生成 Agent
│   └── file.py                 # 文档知识库 Agent
├── anki_card_templates/        # Anki 卡片 HTML/CSS 模板
├── sync.py                     # 单笔记同步引擎
├── sync_all.py                 # 全量批量同步引擎
├── daily_to_atomic.py          # 日记原子化拆分
├── router.py                   # Multi-Agent 路由分发器
├── model_client.py             # Gemini/Qwen 统一模型网关
├── agent_tools.py              # Agent 共享工具函数 (Anki API, PubMed, 向量检索)
├── similarity_manager.py       # 语义相似度织网引擎
├── auto_slide_processor.py     # 幻灯片全自动处理器
├── solve_hw.py                 # 智能作业求解器
├── utils.py                    # 核心工具库 (文档解析, 格式化)
├── vault_index.py              # Vault 索引构建 (带持久化缓存)
├── fix_math.py                 # LaTeX 公式修复工具
├── fix_md.py                   # Markdown 公式清理脚本
├── run_similarity.sh           # 语义关联一键启动脚本
├── .env.example                # 环境变量模板
└── DOCUMENTATION.md            # 详细技术手册
```
