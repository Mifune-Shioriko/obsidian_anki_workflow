import model_client as genai
from model_client import types
import utils

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的核心任务是：批改我提交的选择题测验，并给出反馈。

我会提交一份选择题测验，其中我用 "- [x]" 标记了我选择的选项，未选择的选项用 "- [ ]" 标记。

严格遵守以下规则：

1. 输出结构：
- 首先给出总分，格式为"得分：X/5"（X 为答对的题数）。
- 然后逐题给出反馈。

2. 逐题反馈格式：
- 题号使用中文括号格式：（1）（2）（3）...
- 如果答对：输出"✓ 正确"，然后简要解释为什么正确（1-2 句话即可）。
- 如果答错：输出"✗ 错误"，然后指出正确答案，并解释为什么（2-3 句话）。

3. 反馈原则：
- 解释要简洁明了，不要长篇大论。
- 如果题目是多选题，需要说明哪些选项选对了、哪些选错了或漏选了。
- 全部使用中文。

4. 格式限制：
- 不要使用三级标题。
- 题号与反馈内容之间空一行。
- 不同题目的反馈之间空一行。

输出格式示例（必须严格遵循）：

得分：3/5

（1）✓ 正确。PIP2 确实定位于细胞膜内侧，具有亲水头部和亲油尾部。

（2）✗ 错误。正确答案是"将细胞质中的钙离子泵入内质网储存"。PLC 的主要作用是水解 PIP2，而不是泵钙离子。

（3）✓ 正确。IP3 亲水可游离，DAG 亲脂锚定在膜上。
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
            final_system_prompt += f"\n\n请参考以下当前笔记的正文内容作为知识依据：\n{clean_header}"

    typed_history = []
    for turn in history:
        typed_parts = []
        for p in turn.get("parts", []):
            if isinstance(p, dict) and "text" in p:
                typed_parts.append(types.Part.from_text(text=p["text"]))
        if typed_parts:
            typed_history.append(types.Content(role=turn["role"], parts=typed_parts))

    if not typed_history:
        return "❌ **Grade Agent 错误**: 对话历史中没有文本内容可供批改。"

    try:
        print(f"正在调用 {utils.MODEL_NAME} 批改测验...")
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=typed_history,
            config=types.GenerateContentConfig(
                temperature=0.3,
                system_instruction=final_system_prompt,
            )
        )

        if not res.text:
            return "❌ **Grade Agent 警告**: Google API 返回了空内容。"

        return res.text
    except Exception as e:
        return f"❌ **Grade Agent 发生错误**: {e}"
