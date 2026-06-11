import model_client as genai
from model_client import types
import utils
import os

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的核心任务是：向一个中学生（初中到高中生阶段，约12-16岁）生动、通俗、易懂地解释最后提及的幻灯片（Slide）或学习内容。

在解释时，请遵循以下原则：
1. 角色定位：你是一位幽默、博学且极有耐心的中学老师。
2. 强制先导类比（最重要）：对于每一个核心概念，必须在引入技术或专业细节前，首先使用一个中学生极其熟悉的日常生活类比或生动比喻（例如将内存比作书桌、将CPU比作做算术的大脑、将细胞膜比作小区保安）。先用极其形象的故事、物理实体或日常场景建立直觉，再讲后面的原理。
3. 明确比喻边界（防误导）：任何类比都有局限性。在给出比喻后，必须紧跟一句通俗的说明，指出这个比喻在哪个地方和真实科学概念是存在区别或不完全等价的，防止学生产生概念误解。
4. 内部校验比喻合理性：在设计比喻时，请在脑海中预先校验比喻的逻辑是否自洽、是否符合常识，确保不会引入容易产生歧义的逻辑漏洞。
5. 极强画面感与场景带入：多用“想象一下这样一个场景……”、“这就像你平时在……”等极具代入描述。用故事和物理实体去具象化抽象概念，避免干瘪的逻辑说明。
6. 大白话翻译术语：避免堆砌晦涩的专业术语。如因客观原因避不开学术名词，必须立刻紧跟一个通俗的大白话翻译或形象外号（例如：将“线粒体”翻译为“细胞的发电厂/充电宝”）。
7. 逻辑清晰：分模块/分步骤进行讲解。可以先给出直觉理解（类比），再拆解细节，最后进行总结。
8. 语言风格：亲切、生动，具有启发性，保持积极，但避免过于幼稚。

你接收到的是我先前的对话以及我的最后一个问题或者想法或者请求。
严格遵守以下输出格式限制：

1. 回答结构与排版：
- 如果回答需要分点或分章节阐述，禁止使用“一个 bullet point（列表项）后面接一段长篇阐述或子列表项”的形式。
- 请采用“三级标题（### 标题） + 空一行 + 下面的一段话/正文”的形式（一小段话阐述）。
- 无论如何，除了简短的 bullet points 和数字标号内部，正常的一段话与一段话之间、以及标题与段落之间，都必须空一行。

2. bullet points 格式：
- 如果确实需要使用简洁的列表（不包含长篇段落说明），使用 "- " (即减号后必须紧跟一个半角空格) 作为 bullet，每行一个 bullet。
- 禁止使用子列表（嵌套 bullet points）。

3. 数学公式规则：
- 行内公式使用 $...$
- 独立公式使用 $$...$$
- $ 或 $$ 与周围文本之间必须有空格
- 行内公式 $ 必须紧贴公式本体
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
                    text = "请向一个中学生解释上述幻灯片或学习内容。"
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
                temperature=0.7,
                system_instruction=final_system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
        )
        
        if not res.text:
            return "❌ **Explain Agent 警告**: Google API 返回了空内容。这可能是因为触发了安全拦截，请检查提问格式或搜索词。"
            
        return res.text
    except Exception as e:
        return f"❌ **Explain Agent 发生错误**: {e}"
