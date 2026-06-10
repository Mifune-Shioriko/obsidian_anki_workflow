# Obsidian-Anki AI 整合工作流 (Obsidian-Anki AI Integration Workflow)

这是一个基于 Python 和 Google Gemini (GenAI SDK) 的个人知识管理 (PKM) 自动化工作流项目。它深度整合了 Obsidian、Anki 与多智能体系统 (Multi-Agent System)，为您提供从日常随笔、日记自动拆分、智能制卡、双向同步到智能问答与作业解答的一站式学习与复习解决方案。

## 核心功能与工作流

*   **双向卡片同步 (sync.py 和 sync_all.py)**
    *   将 Obsidian（原子笔记）中的卡片与 Anki 进行双向增删改同步。
    *   **媒体支持**：自动将 Files/ 目录下的 `[[image.png]]` 格式图片上传到 Anki 媒体库。
    *   **暂停与复活机制**：在 Anki 中被暂停的卡片，同步时会在 Obsidian 对应卡片的问题栏中打上 `#auto_suspended` 标签；若想在 Obsidian 中复活（取消暂停）卡片，只需在问题前加上 `#revive`，下次同步将自动取消暂停并清理该标签。
*   **日记/随笔自动拆分 (daily_to_atomic.py)**
    *   自动扫描 Daily Notes/ 中的零碎随笔，利用 AI 提取并精简为带 UUID 的、主题单一清晰的“原子笔记”（Atomic Notes）。
    *   **极简防重名**：若生成的标题与已有笔记重名，代码层会自动在标题后追加递增数字（例如：`标题 1`，`标题 2`），既省 token 又安全，防止文件被意外覆盖。
*   **多智能体路由系统 (router.py 和 agents/)**
    *   高度可扩展的 AI Agent 分发系统。在笔记末尾的 quote 块中输入 `@agent_name <指令>` 即可调用。
    *   内置 Agent 包括：`@add`（制卡）、`@revise`（修改）、`@explain`（解释）、`@pubmed`（学术搜索）、`@tag`（自动标签）、`@map`（关联图谱）、`@reading_suggestions`（阅读推荐）等。
    *   **草稿确认协议 (Draft-Confirm Protocol)**：`@add` 和 `@revise` 在执行时，会先输出由 Pydantic 校验的 JSON 草稿表格。只有当收到后续确认命令（如 y、yes、ok 或 确认）时，才会真正更新笔记末尾的“卡片”区域。
*   **智能作业助手 (solve_hw.py)**
    *   支持读取 PDF 或文本。结合 Gemini 自动解析作业题目，给出高度精确、简练的数理或专业解答。
*   **语义相似度关联 (run_similarity.sh)**
    *   本地 Qdrant 向量数据库驱动。一键运行 `bash run_similarity.sh`，自动扫描当前库并为笔记推荐语义上最相关的其他笔记。

## Obsidian 插件依赖与关键配置

为使卡片能完美反向跳转至 Obsidian 原文，且同步引擎能正确追踪卡片，您需要在 Obsidian 中安装并配置以下插件：

1.  **Advanced URI 插件** (必须)
    *   在 Obsidian 插件市场搜索并安装 Advanced URI 并启用。
    *   同步脚本生成的跳转链接格式为 `obsidian://advanced-uri?vault=<库名>&uid=<UUID>`。
2.  **Frontmatter UUID 支持**
    *   原子笔记必须在 YAML Frontmatter 中包含唯一的 `id`（例如 `id: "your-uuid-here"`）。
    *   这是 Advanced URI 进行全局精准定位与跳转的唯一凭证，也是同步引擎清理废弃卡片（当您删除笔记或移除卡片区时）的核心追踪依据。

## 数学公式 (LaTeX) 格式规范

*   **Obsidian / Markdown 环境**：使用标准的 `$inline$`（行内）和 `$$block$$`（行间）公式。
*   **Anki 卡片环境**：由 Agent 自动生成或同步的卡片，其内部公式**必须**使用 `\(...\)`（行内）和 `\[...\]`（行间）格式。**卡片内部严禁出现 `$` 或 `$$`**，以保证 Anki 原生 MathJax 的完美渲染。

## 环境依赖与配置

### 1. 软件前置条件
*   **Python 3.10+**
*   **Anki 客户端**：保持后台运行，并安装 AnkiConnect 插件（确保端口 8765 可用）。
*   **外部二进制依赖**（仅作业助手需要）：
    *   `pandoc`（用于解析文档格式）
    *   `soffice`（LibreOffice Headless，用于 PPTX 转码）

### 2. 安装 Python 依赖
```bash
pip install google-genai pymupdf requests markdown python-dotenv pydantic
```
*(注意：项目已全面升级，排他性地使用全新的谷歌官方 GenAI SDK `google-genai`，不再使用旧版 `google-generativeai`)*

### 3. 配置环境变量
在项目根目录下，基于 `.env.example` 创建 `.env` 文件，并填写您的配置：
```ini
GOOGLE_API_KEY="您的_gemini_api_key"
VAULT_DIR="/您的/obsidian/库路径"
ANKI_URL="http://127.0.0.1:8765"
ANKI_DECK_NAME="Obsidian"
ANKI_NOTE_TYPE="Obsidian"
OBSIDIAN_VAULT_NAME="您的_obsidian_库名称"
```

## 常用运行命令

*   **执行双向同步**：`python sync.py`（或 `python sync_all.py` 进行全量处理）
*   **处理日记/随笔拆分**：`python daily_to_atomic.py`
*   **运行语义相关性分析**：`bash run_similarity.sh`
*   **作业智能求解**：`python solve_hw.py <文件路径>`
*   **调用多 Agent 路由**：`python router.py <笔记路径>`
