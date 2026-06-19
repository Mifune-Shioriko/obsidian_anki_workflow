import model_client as genai
from model_client import types
import utils

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的核心任务是：根据我提供的对话历史，出 5 道选择题来考察我对其中知识的理解程度。

严格遵守以下规则：

1. 题目数量：硬性要求，必须且只能出 5 道题。

2. 题目类型：
- 全部为选择题，包含单选和多选。
- 单选或多选由你根据题目内容自行判断。
- 多选题必须在题干末尾标注"（多选）"。

3. 选项格式：
- 每个选项使用 "- [ ] " 格式（减号 + 空格 + 方括号 + 空格 + 选项内容）。
- 每道题的选项数量由你根据题目内容自行决定（3 到 5 个均可）。

4. 出题原则：
- 题目应覆盖对话中讨论过的核心概念和关键细节。
- 题目应有区分度，不要全是送分题，也不要全是刁钻题。
- 选项中的干扰项应当合理，能考察真实理解而非死记硬背。

5. 格式限制（严格遵守）：
- 题号必须使用中文括号格式：（1）（2）（3）...，不要使用"1."这种格式。
- 不要使用三级标题。
- 不要输出答案或解析，只输出题目。
- 全部使用中文。
- 题干与选项之间必须空一行。
- 不同题目之间必须空一行。

输出格式示例（必须严格遵循）：

（1）以下关于 xxx 的说法，正确的是？

- [ ] 选项 A
- [ ] 选项 B
- [ ] 选项 C
- [ ] 选项 D

（2）以下哪些属于 xxx 的特征？（多选）

- [ ] 选项 A
- [ ] 选项 B
- [ ] 选项 C
"""

def handle(command, history, note_path, full_content):
    all_chat_text = ""
    for turn in history:
        for part in turn.get("parts", []):
            if isinstance(part, dict) and "text" in part:
                all_chat_text += part["text"] + "\n"
            elif not isinstance(part, dict) and hasattr(part, 'text') and getattr(part, 'text', None):
                all_chat_text += getattr(part, 'text') + "\n"

    _, context_header, _, _, _ = utils.parse_document(full_content)

    final_system_prompt = SYSTEM_PROMPT_BASE

    if context_header.strip():
        clean_header = context_header.strip()
        if clean_header.endswith("---"):
            clean_header = clean_header[:-3].strip()
        if clean_header:
            final_system_prompt += f"\n\n请参考以下当前笔记的正文内容来出题：\n{clean_header}"

    typed_history = []
    for turn in history:
        typed_parts = []
        for p in turn.get("parts", []):
            if isinstance(p, dict) and "text" in p:
                typed_parts.append(types.Part.from_text(text=p["text"]))
        if typed_parts:
            typed_history.append(types.Content(role=turn["role"], parts=typed_parts))

    if not typed_history:
        return "❌ **Quiz Agent 错误**: 对话历史中没有文本内容可供出题。"

    try:
        print(f"正在调用 {utils.MODEL_NAME} 生成测验...")
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=typed_history,
            config=types.GenerateContentConfig(
                temperature=0.7,
                system_instruction=final_system_prompt,
            )
        )

        if not res.text:
            return "❌ **Quiz Agent 警告**: Google API 返回了空内容。"

        return res.text
    except Exception as e:
        return f"❌ **Quiz Agent 发生错误**: {e}"
