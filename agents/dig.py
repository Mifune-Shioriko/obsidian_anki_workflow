from google import genai
from google.genai import types
import utils
import os

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = r"""你现在的核心任务是：总结先前的解释和讨论，并提炼出高度原子化、易于记忆且极度适合用来制作记忆卡片（Anki）的知识文本。

在总结和提炼时，请遵循以下原则：
1. 去粗取精（剔除类比与闲聊）：先前的解释（如由 @explain 产生的内容）包含大量日常生活比喻、故事和启发性引入。你的总结必须完全剥离这些非学术的比喻和过渡句，回归严谨、学术、标准的科学与专业表述。
2. 提取核心考点：重点提取并精炼出核心概念的学术定义、关键步骤、核心原理、关键区别以及核心公式。
3. 极限单知识点句式（最重要）：每一句话（每一个 bullet point）必须尽量只包含【一个】最微小的单一知识点。严禁把多个细节、原因或步骤合并到长句中，避免使用复杂的从句或连词。确保句子极其短小、直接、因果单一，方便读者一目了然。
4. 极佳的制卡亲和力：将知识点整理成最容易被提炼为 Anki 卡片的短句、定义或对比列表。语言必须自包含、去语境化，确保读者可以非常直观地通过加粗（**）核心词汇来准备制卡。
5. 语言风格：严谨、简练、专业、结构化，无任何口水话和寒暄。

严格遵守以下输出格式限制（非常重要，必须严格执行）：
1. 必须采用且仅采用“三级标题（### 标题） + 空一行 + 多个无嵌套的 bullet points”形式。每个三级标题下绝对禁止出现纯文本段落，必须全部由 bullet points 组成。
2. 每个标题之间、标题与 bullet 之间、以及两个三级标题块之间，都必须空一行。
3. bullet points 格式：
   - 使用 "- " (即减号后必须紧跟一个半角空格) 作为 bullet，每行一个 bullet，禁止使用嵌套/子列表。
4. 数学公式规则：
   - 行内公式使用 $...$，独立公式使用 $$...$$。
   - $ 或 $$ 与周围文本之间必须有空格。

【优秀输出格式范例】
### 近似公式与成立条件

- 核心近似公式为 $\sqrt{1+x} \approx 1 + \frac{x}{2}$ 。
- 该近似公式成立的充要条件是 $|x| \ll 1$ 。
- 变量 $x$ 的绝对值越接近 $0$ ，近似值的精确度越高。
- 变量 $x$ 的绝对值偏离 $0$ 越远，近似值的误差越大。
- 变量 $x$ 既可以取正值，也可以取负值。

### 数学原理与幂级数展开

- 该近似公式的数学理论基础是泰勒公式。
- 该近似公式也可以通过广义二项式定理推导得出。
- 函数 $f(x) = \sqrt{1+x}$ 在 $x = 0$ 处的展开式为 $\sqrt{1+x} = 1 + \frac{1}{2}x - \frac{1}{8}x^2 + \dots$ 。
"""

def handle(command, history, note_path, full_content):
    # 1. 安全地提取所有纯文本内容（兼容 router 传来的可能带有对象的 history）
    all_chat_text = ""
    for turn in history:
        for part in turn.get("parts", []):
            if isinstance(part, dict) and "text" in part:
                all_chat_text += part["text"] + "\n"
            elif not isinstance(part, dict) and hasattr(part, 'text') and getattr(part, 'text', None):
                all_chat_text += getattr(part, 'text') + "\n"

    # 2. 建立索引并提取双链与图片
    vault_index = utils.build_vault_index(utils.VAULT_DIR)
    linked_context, linked_images = utils.extract_linked_context(all_chat_text, vault_index)

    # 解析当前笔记的正文内容作为上下文
    _, context_header, _, _, _ = utils.parse_document(full_content)
    
    final_system_prompt = SYSTEM_PROMPT_BASE
    
    # 注入当前笔记正文作为上下文 (只使用被截断后的 context_header)
    if context_header.strip():
        # 移除最后的 --- 分隔符以免混淆
        clean_header = context_header.strip()
        if clean_header.endswith("---"):
            clean_header = clean_header[:-3].strip()
        if clean_header:
            final_system_prompt += f"\n\n请参考以下当前笔记的正文内容来辅助回答：\n{clean_header}"

    if linked_context:
        final_system_prompt += f"\n\n请参考以下我提到的相关笔记内容来辅助回答：\n{linked_context}"

    # 3. 【关键修复】构建强类型的 typed_history，保留非文本部分
    typed_history = []
    for turn in history:
        typed_parts = []
        for p in turn.get("parts", []):
            # 如果是纯文本字典，转换为 SDK 对象
            if isinstance(p, dict) and "text" in p:
                text = p["text"]
                # 如果是最后一条消息且内容是默认的“请继续执行”，自动优化为具体的解释指令
                if turn == history[-1] and text.strip() == "请继续执行":
                    text = "请提炼并总结上述讨论的所有核心知识点。"
                typed_parts.append(types.Part.from_text(text=text))
            # 如果 router 之前已经塞了图片对象过来，直接保留它！
            elif not isinstance(p, dict):
                typed_parts.append(p)
        typed_history.append(types.Content(role=turn["role"], parts=typed_parts))

    # 4. 加载并在最后一次提问的 parts 中追加图片
    for img_path in linked_images:
        try:
            with open(img_path, 'rb') as f:
                img_bytes = f.read()
                
            ext = img_path.suffix.lower()
            mime_type = "image/png"
            if ext in ['.jpg', '.jpeg']: mime_type = "image/jpeg"
            elif ext == '.webp': mime_type = "image/webp"
            elif ext == '.gif': mime_type = "image/gif"
            
            # 将图片二进制直接拼入最新的那段对话里
            typed_history[-1].parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
            )
            print(f"已将图片作为视觉上下文加载给 AI: {img_path.name}")
        except Exception as e:
            print(f"Error: 图片加载失败 {img_path}: {e}")

    # 5. 请求大模型
    try:
        print(f"正在调用 {utils.MODEL_NAME} 生成回复...")
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=typed_history,
            config=types.GenerateContentConfig(
                temperature=0.3,  # 调低温度确保总结的科学性与客观度
                system_instruction=final_system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
        )
        
        if not res.text:
            return "❌ **Dig Agent 警告**: Google API 返回了空内容。这可能是因为触发了安全拦截，请检查提问格式或搜索词。"
            
        return res.text
    except Exception as e:
        return f"❌ **Dig Agent 发生错误**: {e}"
