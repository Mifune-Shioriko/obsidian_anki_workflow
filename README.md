# Obsidian-Anki AI Workflow

基于 Google Gemini API 的个人知识管理自动化工具集。这套脚本旨在实现从“每日笔记”到“原子化知识”，再到“Anki 记忆卡片”的无缝、自动化流转。

## 核心功能 (Scripts)

本项目包含四个独立但可协同工作的 Python 脚本：

- **`daily_to_atomic.py`**：自动扫描当天的“Daily Notes”，提取底部的草稿/随记内容，调用 AI 自动生成合适的标题，并将其转化为独立的原子笔记 (Atomic Notes)，同时在原日记中留下双链。
- **`main.py`**：核心制卡引擎。读取指定的 Obsidian 笔记，根据预设的 Prompt 提取核心知识点，生成精炼的问答卡片，并自动写入 Anki 及原笔记的 Markdown 表格中。支持处理数学公式与图片。
- **`sync.py`**：全量扫描原子笔记目录，对比 Obsidian 与 Anki 中的卡片状态。支持双向内容更新、废弃卡片清理以及缺失卡片的补录，确保两端数据绝对一致。（以 Obsidian 中保存的数据为准）
- **`answer.py`**：AI 问答助手。读取当前日记中的对话上下文（支持双链引用和图片解析），调用 Gemini 生成排版规范的回答，并自动追加到笔记末尾。

## 环境准备 (Prerequisites)

1. **Python 3.x**
2. **Obsidian 插件**：需安装并启用 [Advanced URI](https://github.com/Vinzent03/obsidian-advanced-uri)（用于 Anki 回跳 Obsidian）。
3. **Anki 插件**：需安装并启用 [AnkiConnect](https://ankiweb.net/shared/info/2055492159)（确保 Anki 处于打开状态）。
4. **API 密钥**：需要申请 [Google Gemini API Key](https://aistudio.google.com/)。

## 快速开始 (Getting Started)

### 1. 克隆仓库

```bash
git clone [https://github.com/Mifune-Shioriko/obsidian_anki_workflow.git](https://github.com/Mifune-Shioriko/obsidian_anki_workflow.git)
cd obsidian_anki_workflow

```

### 2. 安装依赖

建议使用虚拟环境，然后安装所需的 Python 库：

```bash
pip install google-genai python-dotenv requests markdown pydantic

```

### 3. 配置环境变量

复制根目录下的配置模板，并填入你自己的绝对路径和 API Key：

```bash
cp .env.example .env

```

用你喜欢的编辑器打开 `.env`，补全 `GOOGLE_API_KEY` 和 `VAULT_DIR`（你的 Obsidian 仓库本地绝对路径）。


