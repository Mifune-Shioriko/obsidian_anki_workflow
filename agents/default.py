from google import genai
from google.genai import types
import utils
import os

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的任务是回答我最后的问题。你接收到的是我先前的对话以及我的最后一个问题或者想法或者请求。
严格遵守以下输出格式限制：

1. 回答结构与排版：
- 如果回答需要分点或分章节阐述，禁止使用“一个 bullet point（列表项）后面接一段长篇阐述或子列表项”的形式。
- 请采用“三级标题（### 标题） + 空一行 + 下面的一段话/正文”的形式。
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
                typed_parts.append(types.Part.from_text(text=p["text"]))
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
            return "❌ **Default Agent 警告**: Google API 返回了空内容。这可能是因为触发了安全拦截，请检查提问格式或搜索词。"
            
        return res.text
    except Exception as e:
        return f"❌ **Default Agent 发生错误**: {e}"
