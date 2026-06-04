# Obsidian-Anki AI 集成工作流技术开发与使用手册

本手册旨在为 Obsidian-Anki AI 集成工作流提供详尽的技术架构设计、核心逻辑机制、双向同步协议、Multi-Agent 路由设计以及卡片制作黄金标准的参考指南。

---

## 1. 系统架构与数据流向

本系统是一个高度自动化的个人知识管理（PKM）闭环。它深度集成了 Obsidian 本地笔记库、Anki 记忆卡片软件、Qdrant 向量数据库，并通过 Google Gemini API（采用最新的 Google GenAI SDK 强类型接口）提供智能辅助。

### 1.1 系统核心闭环
- **信息采集与原子化**：用户在 Obsidian 的日记中随意记录想法。系统自动扫描并将底部的草稿提取为独立的「原子笔记（Atomic Notes）」，并自动注入唯一且稳定的 YAML UUID `id`。
- **知识建网与语义检索**：笔记创建后，系统调用 `gemini-embedding-2` 生成 3072 维向量，存储至 Qdrant 中。通过向量最近邻检索（余弦相似度），动态寻找每篇笔记最关联的 15 篇笔记，自动重写各笔记底部的「相关笔记」区域，打破信息孤岛。
- **卡片渲染与同步**：笔记中的「## 卡片」markdown 表格由同步引擎自动解析。卡片内公式、图片链接会被动态处理。媒体资源无损上传至 Anki，普通文本转换为安全的 HTML 格式。新增卡片在 Anki 生成后，其唯一的 Note ID 会被反向写回 Obsidian 卡片表格的第三列。
- **状态联动与复活协议**：在同步过程中，系统会自动监听 Anki 中的卡片挂起状态。在 Anki 中挂起的卡片会自动在 Obsidian 中加上 `#auto_suspended` 标签。若在 Obsidian 中为其添加 `#revive` 指令，同步引擎会自动在 Anki 中解挂并清除标记。

### 1.2 关键技术栈与依赖
- **语言与底层 SDK**：Python 3.10+，`google-genai`（Google GenAI 强类型 SDK，严禁使用旧版 `google-generativeai`）。
- **向量检索库**：`qdrant-client` 配合本地/远程 Qdrant 向量存储实例。
- **文档与多媒体处理**：`pymupdf (fitz)` 用于 PDF 文本及 bounding box 图像坐标截取；`requests` 与 `httpx` 用于 API 通讯。
- **外部编译与办公套件**：`pandoc`（用于将 Markdown 转 LaTeX TeX 源码）和 LibreOffice `soffice`（用于 headless 模式下将 PPTX 幻灯片转为 PDF 进而由大模型上传读取）。
- **Anki 服务**：本地运行的 Anki 客户端，需装有 `AnkiConnect` 插件并监听本地 `8765` 端口。

---

## 2. 核心模块技术机制解析

### 2.1 双向卡片同步引擎 (sync.py)
同步引擎负责将 `Atomic Notes/` 下所有笔记内的 markdown 卡片表格同步到 Anki。

#### 2.1.1 媒体资源无损同步协议
系统自动检测笔记中的双链图片链接，支持以下匹配模式：
- `![[image.png]]`
- `[[image.png]]`
- 携带缩放参数的格式，例如 `[[image.png|300]]`

**流程**：
1. 正则匹配图片文件名，并在 Vault 的 `Files/` 目录中定位物理文件。
2. 存在对应物理图片时，通过 AnkiConnect 的 `storeMediaFile` 接口将图片上传至 Anki 的媒体管理器。
3. 同步时将 Obsidian 双链图片原位平替为标准 HTML 标签 `<img src="image.png">`。

#### 2.1.2 LaTeX 公式防损坏渲染算法
为了防止 python-markdown 渲染引擎在将 Markdown 转换为 HTML 时，损坏 LaTeX 里的下划线（`_`，会被误渲染为斜体）、星号（`*`）或各种控制字符，系统采用以下机制：
1. **提取与占位**：在渲染前，首先通过正则表达式匹配所有的块级公式 `$$...$$` 和行内公式 `$...$`。
2. **生成占位符**：将每个提取出来的 LaTeX 文本存入字典，并生成不带任何 Markdown 保留字的安全纯英文字母占位符（如 `MATHBLOCKPLACEHOLDER0K` 和 `MATHINLINEPLACEHOLDER0K`）。
3. **HTML 渲染**：调用 markdown 模块（启用 `extra`、`nl2br` 和 `codehilite` 扩展）对纯字母文本进行安全的 HTML 渲染。
4. **占位符还原**：渲染完成后，将对应的占位符还原。同时，在还原过程中，**自动将块级公式替换为 Anki 兼容的 `\[...\]` 语法，行内公式替换为 `\(...\)` 语法**。

#### 2.1.3 挂起与复活机制 (Suspension & Revival)
- **挂起追踪**：Anki 客户端中手动或因到期自动挂起的卡片（其 Note 在 Anki 中被打上 `auto_suspended` 标签），在同步运行时，其对应的 Obsidian 笔记行内会自动被注入 `#auto_suspended` 前缀。
- **复活指令**：如果用户需要在 Obsidian 中解除该卡片的挂起，只需在卡片的问题列最前端加上 `#revive` 前缀。下次同步时，引擎检测到该指令，会调用 Anki API 的 `removeTags` 和 `unsuspend`，在 Anki 端彻底复原该卡片，并自动擦除 Obsidian 表格中的 `#revive` 指令，实现无缝操作。

#### 2.1.4 全局孤儿清理机制
同步引擎维护一个活跃 Obsidian YAML Note ID 的集合。在扫描结束时，引擎向 Anki 查询所有含有 `obsidian://advanced-uri?vault=...&uid=...` 的卡片。若其 `uid` 不在当前库中（表明用户删除了该原子笔记，或者移除了卡片区），系统会调用 `deleteNotes`，在 Anki 侧彻底清理该张废弃卡片，保持全局数据库一致、健康。

### 2.2 每日日记自动切分器 (daily_to_atomic.py)
自动将用户每日随笔日记进行知识重构与原子化切分。

- **草稿区域识别**：扫描 `Daily Notes/YYYY-MM-DD.md`（日记名称以当天日期命名），寻找最后一个出现的 `---` 水平分割线。该线之后的内容被视为今日草稿。
- **标题智能生成**：调用 `gemini-2.5-flash` 模型。系统将草稿投递给大模型，并设定强制响应类型为 `application/json`，从而稳定解析出适合作为文件名的中文标题。
- **文件生成与回链**：在 `Atomic Notes/` 下创建一个带有 YAML Frontmatter（包含创建日期、标题、以及唯一的 UUID `id`）的新 Markdown 文件。同时，在原日记的草稿线前插入该原子笔记的 Wiki 链接 `[[New Title]]`，最后在日记底部自动补齐新的 `---` 分割线，方便随时写新草稿。

### 2.3 语义相似度双链织网引擎 (similarity_manager.py)
负责建立整个原子笔记库的语义关联。

- **嵌入向量计算**：使用 Google 最新推出的 3072 维文本嵌入模型 `gemini-embedding-2`，确保语义向量具有极高和极精确的区分度。
- **增量缓存设计**：为了避免每次扫描都向 Google API 请求计算不必要的 Embedding，系统维护一个本地的 `.similarity_cache.json`。
  - 缓存中记录每个文件的 `hash` (对 pure_body 提取的 SHA256)、`relative_path`、`title` 和修改时间 `mtime`。
  - 若文件未发生改变（hash、路径与修改时间一致），系统直接从 Qdrant 中 Retrieve 现有向量；若检测到新增或修改，则重新调用 Google API 计算，并同步 Upsert 向量至 Qdrant。
- **关联覆写与物理同步**：
  - 基于 Cosine 距离。系统为每篇笔记在集合中查询与其最相关的笔记（排他自身后，选取最多 15 篇，且相似度必须大于阀值 0.72）。
  - 在每个 Markdown 笔记的「## 卡片」区域之前（如无卡片区则在末尾），自动覆写并重构「## 相关笔记」区域。
  - 当本地有笔记被物理删除时，系统在比对中发现其已不在活动扫描列表中，会自动在 Qdrant 库中执行 Delete 销毁其对应点。

### 2.4 学术级物理/数学作业求解器 (solve_hw.py)
面向复杂数理、工科作业的完整自动化求解与排版生成流水线。

- **多模态图表高保真裁剪算法**：
  1. 通过 Google 2.5 Flash 结合预设的 Pydantic Schema (`ProblemListResponse`)，在 Gemini 云端初步上传并分析 PDF 课件/作业原档。
  2. 提取出每个顶级题目的文本、页码以及可能存在的插图/图表的归一化二维边界坐标 `box_2d` (ymin, xmin, ymax, xmax)。
  3. 使用 PyMuPDF (`fitz`) 加载 PDF，将归一化坐标换算为实际像素坐标，对作业图表进行高清局部裁剪并保存到 `~/.cache/hw_solver/hw_images` 下。
- **分步多模态精确求解**：
  - 不采用合并上传（防止长上下文对多模态分析产生图表混淆）。系统串行遍历每道题目。对于标明需要插图的题目，仅将对应的局部高清 PNG 作为视觉输入连同精细 prompt 一起提交给 `gemini-2.5-flash`；对无图题目，则以纯文本输入。
- **风格压缩 passes**：
  - 模型生成首版答案后，系统启动 `compress_solution_style` 轻量级风格规范 pass。
  - 强制将推导过程重构为极度简短、标准的 **Homework-5 学术递交体**：仅保留关键定义、主公式链、参数带入计算、最终答案，通常在 2-8 行内，不允许任何口语化的废话。
- **Pandoc 编译与数学格式纠偏**：
  - 替换 AI 产生的转义符 `\$`，对块级 `$$` 换行符进行格式化（块公式前后留有空行）。
  - 纠正行内公式紧邻 `$` 符号内部的空格。
  - 最后，调用本地 `pandoc` 工具生成一份极具专业质感的 LaTeX 源码 `.tex` 并保留 Markdown 缓存。

---

## 3. Multi-Agent 路由与 Agent 群体设计

### 3.1 路由分发逻辑 (router.py)
对话分发器支持在 Obsidian 笔记内部直接唤醒各种细分领域的专业 Agent。

- **Chat 历史解析协议**：
  - 用户的提问在 Obsidian 中以 markdown 引用块语法表示（以 `> ` 或 `>` 开头的行）。
  - 大模型的回答在引用块外部直接显示。
  - `utils.parse_markdown_to_history` 负责解析交替出现的 user 和 model 角色，若首条消息没有 `> ` 前缀，系统会进行智能纠偏，将其转化为 user 角色防止 SDK 报错。
- **动态路由映射**：
  - 通过 `importlib` 动态加载 `agents/` 下所有除 `__init__.py` 外拥有 `handle` 函数的 Python 脚本。
  - 识别用户最后一次 `> ` 提问中的前缀（如 `@add`、`@revise`、`@pubmed`、`@file` 等）。若无前缀，默认路由给 `default` 模块。
- **格式清洗与插回 (`utils.sanitize_format`)**：
  - **加粗限制**：自动将模型回复中的所有 Markdown 粗体标签（`**`）移除。
  - **列表对齐**：统一将列表符号规范化为 `-`，自动建立缩进层级栈，多层列表转换为 Tab 对齐（非空格对齐），保障 Obsidian 的展示美观。

### 3.2 Agent 群体功能图谱
本项目设计了一套角色分明、能力互补的 Agent 矩阵：

| Agent 命令 | 模块文件 | 核心职责 | 技术实现细节 |
| :--- | :--- | :--- | :--- |
| `@default` | `default.py` | 基础问答与多模态双链加载 | 构建 `build_vault_index` 寻找提问中的 `[[笔记名]]`，自动读取内容并提取关联图片二进制传输。 |
| `@add` | `add.py` | 纯新增 Anki 记忆卡片 | 草稿-确认双阶段模式。调用 Gemini 生成卡片草稿 JSON 表格。回复 `@add ok` 后通过 `rewrite_markdown_table` 追加至笔记底部卡片表格。 |
| `@revise` | `revise.py` | 修改、拆分与维护现有卡片 | 草稿-确认双阶段模式。在 Anki 复习模式下运行能自动提取当前复习卡片的 ID 上下文进行精准修订。 |
| `@tag` | `tag.py` | 卡片打标签专员 | 自动结合上下文提取卡片 ID，支持 Tool Function 调用批量修改 Anki 端的卡片分类标签。 |
| `@pubmed` | `pubmed.py` | 医学与边缘学术理论严谨论证 | 先通过 `search_web` 定位理论或网红医生的主张，再通过 `search_pubmed` (NCBI Web Service) 抓取学术文献。强制要求输出 PMID 超链接，严禁捏造学术编号。 |
| `@file` | `file.py` | 多文档知识库 NotebookLM | 使用绝对路径提取及 LibreOffice 转换机制。对多 PDF/PPTX 上传并缓存在 `.notebooklm_cache.json` 中，提供纯粹基于专属库的问答。 |
| `@reading_suggestions` | `reading_suggestions.py` | 知识脱水与延伸学习领航员 | 向 Anki 请求今日已复习卡片（`rated:1`），通过提炼零散的卡片骨架，输出血肉填充深读指南，并提出 3-4 个教材才能解答的靶向深层问题。 |
| `@new_overview` | `new_overview.py` | 新学知识梳理与脉络复盘 | 抓取今日在 Anki 中首次引入的新学卡片（`introduced:1`），为用户提炼成宏观逻辑脉络。 |
| `@review_overview` | `review_overview.py` | 每日复习内容宏观自测总结 | 抓取今日所有评过分的卡片内容，进行一站式逻辑梳理与自测回顾。 |
| `@map` | `map.py` | 认知边界拓展与盲区预测导师 | 基于用户特定主题检索卡片（宽泛主题用向量检索，具体词汇用普通检索），评估学习层级，并预测该主题下的学习盲区，给出建议。 |

---

## 4. Spaced Repetition 卡片制作黄金标准 (prompt.txt)

所有制卡 Agent 严格遵守 **`prompt.txt`** 中的卡片制作黄金法则。这些标准保证了卡片在间隔重复中能达到最高能效。

### 4.1 核心原则
- **极致原子化 (Extreme Atomization)**：这是卡片制作的最高原则。**每一张卡片必须且只能测试一个不可再分的、单一的事实或概念**。答案部分应极度精简，通常只包含一个或几个关键词。若概念复杂，大模型在内部必须先将其拆解为核心事实清单，再转化为对应的独立卡片。
- **Top 20% 原则（二八定律）**：拒绝臃肿。面对一段长文本材料，**仅提炼并保留最核心、最重要的前 20% 的概念卡片**（向下向上取整），丢弃所有周边次要信息，确保学习精力集中在高价值节点。
- **完全独立自包含 (Self-Contained)**：问题必须直接、无歧义，禁止在卡片问题和答案中出现特定上下文指代词（如“该例中”、“题目里”、“如图所示”）。任何卡片在脱离笔记正文单独抽出来复习时，都必须能被清晰解答。
- **排除例题原则**：绝对禁止直接将练习题、例题步骤或特定数值记忆封装进卡片，只记忆其背后普遍适用的定则、公式定义或基础理据。

### 4.2 LaTeX LaTeX 公式兼容性规范 (MathJax)
为了保障 Anki 卡片在各类客户端（包括桌面端、iOS 移动端 AnkiMobile 及安卓端 AnkiDroid）的 MathJax 渲染一致性，卡片内容**必须**严格执行以下规则：
- **格式定界符规范**：
  - 行内公式（在句子行内出现的数学符号、单字母变量等）**必须**包裹在 `\(...\)` 符号内。
  - 独立成行的块级公式**必须**包裹在 `\[...\]` 符号内。
  - **绝对禁止在卡片中使用 `$` 或 `$$`**。
- **排版对齐**：变量符号或公式定界符与中文文字之间应保留一个空格，例如：
  - 正确：`求解方程 \(x + 5 = 10\) 中的自变量 \(x\)`。
  - 错误：`求解方程\(x + 5 = 10\)中的自变量\(x\)`。

---

## 5. 环境配置与部署维护

### 5.1 环境配置清单 (.env)
在项目根目录下，通过创建 `.env` 文件来驱动整个系统的正常运作：

```ini
# Gemini LLM API 密钥 (必须)
GOOGLE_API_KEY="your_google_gemini_api_key_here"

# 本地 Obsidian 库的绝对物理路径 (必须)
VAULT_DIR="/home/shioriko/Share/Document/Note7"

# Anki 运行及 AnkiConnect 的本地 API 地址 (默认 http://127.0.0.1:8765)
ANKI_URL="http://127.0.0.1:8765"

# 同步到 Anki 的目标牌组名称
ANKI_DECK_NAME="Obsidian"

# 同步到 Anki 的目标模板类型名称
ANKI_NOTE_TYPE="Obsidian"

# 你的 Obsidian 库在 URL 协议中的 Vault 标识名称
OBSIDIAN_VAULT_NAME="Note7"

# Qdrant 向量检索服务地址 (默认 http://localhost:6333)
QDRANT_URL="http://localhost:6333"
```

### 5.2 向量数据库容器启动 (run_similarity.sh)
系统依托 Qdrant 运行向量检索。建议通过 docker 快速拉起服务，系统维护的启动快捷指令结构如下：

```bash
#!/bin/bash
# run_similarity.sh
# 启动 Qdrant Docker 实例
docker run -d -p 6333:6333 -p 6334:6334 \
    -v qdrant_storage:/qdrant/storage \
    --name qdrant \
    qdrant/qdrant:latest

# 激活本地虚拟环境并启动语义关联构建
source /home/shioriko/scripts/Obsidian/venv/bin/activate
python /home/shioriko/scripts/Obsidian/Anki/similarity_manager.py
```

### 5.3 常见系统环境排错
1. **Pandoc TeX 转换失败**：数理公式求解器将 Markdown 转 TeX 时，若系统缺少 `pandoc` 二进制，会发出转换警告。请确保系统执行了 `sudo apt install pandoc`。
2. **PPTX 转换 PDF 挂起**：智能讲义 NotebookLM 转换 PPTX 时需要本地运行 headless LibreOffice 实例。排查系统中是否包含 `soffice` 命令（可通过安装 `libreoffice` 解决）。
3. **Anki Connect 报错 "connection refused"**：确保 Anki 已经打开，且 AnkiConnect 插件设置中允许了 `127.0.0.1` 的 POST 跨域通信。
