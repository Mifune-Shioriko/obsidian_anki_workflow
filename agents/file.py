from google import genai
from google.genai import types
import utils
import os
import re
import json
import time

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的角色是类似于 NotebookLM 的智能学习助教。
系统会向你提供一份或多份 PDF 文档（如课件、书籍等）作为你的专属知识库。
请仔细阅读这些提供的文档内容，并在回答问题时遵守以下原则：

1. **基于文档回答**：优先使用文档中的信息回答问题。如果用户的提问超出了文档范围，请明确指出“文档中未提及此内容”，然后再根据你的基础知识进行补充解答。
2. **准确引用**：尽可能在回答中指出信息的来源（例如：结合课件第二页的图表，或者根据 xx 章节）。
3. **结构清晰**：请使用自然段、bullet points、表格等形式使回答易于阅读。
4. **格式规范**：行内公式使用 $...$，独立公式使用 $$...$$，确保公式与周围中文字符之间有空格隔开。禁止使用粗体（**粗体**）。
"""

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".notebooklm_cache.json")

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Warning: Failed to save cache: {e}")

def get_or_upload_pdf(pdf_path):
    cache = load_cache()
    abs_path = os.path.abspath(pdf_path)
    if not os.path.exists(abs_path):
        return None

    mtime = os.path.getmtime(abs_path)
    original_name = os.path.basename(abs_path)
    
    if abs_path in cache:
        cached_info = cache[abs_path]
        if cached_info.get('mtime') == mtime:
            try:
                # 检查线上是否依然存在 (有效期48小时)
                remote_file = client.files.get(name=cached_info['name'])
                if remote_file.state.name == 'ACTIVE':
                    print(f"📚 {original_name} (Cached: {remote_file.name})")
                    return cached_info
            except Exception as e:
                print(f"🔄 缓存失效，正在重新上传: {original_name}")
        else:
            print(f"🔄 文件已修改，正在重新上传: {original_name}")
    else:
        print(f"📤 正在上传: {original_name}")
        
    try:
        uploaded = client.files.upload(file=abs_path)
        
        while uploaded.state.name == 'PROCESSING':
            print('.', end='', flush=True)
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
            
        if uploaded.state.name == 'FAILED':
            print(f"❌ 文件处理失败: {abs_path}")
            return None

        print(f"✅ 上传完成: {original_name} ({uploaded.name})")
        
        file_info = {
            'name': uploaded.name,
            'uri': uploaded.uri,
            'mime_type': uploaded.mime_type,
            'mtime': mtime,
            'original_name': original_name
        }
        cache[abs_path] = file_info
        save_cache(cache)
        return file_info
    except Exception as e:
        print(f"❌ 上传出错 {abs_path}: {e}")
        return None

def extract_existing_paths(text):
    valid_paths = set()
    
    # 1. 匹配带引号或反引号的路径
    quoted_paths = re.findall(r'[\'"`](/[^\'"`]+)[\'"`]', text)
    for p in quoted_paths:
        if os.path.exists(p):
            valid_paths.add(p)
            
    # 2. 匹配不带空格的路径
    unquoted_paths = re.findall(r'(/[^\s"\'\`]+)', text)
    for p in unquoted_paths:
        # 去除末尾的常见标点符号
        p = p.rstrip('.,;:!?。，；：！？')
        if os.path.exists(p):
            valid_paths.add(p)
            
    return valid_paths

def gather_pdfs_from_paths(paths):
    pdf_files = set()
    for p in paths:
        if os.path.isfile(p):
            if p.lower().endswith('.pdf'):
                pdf_files.add(p)
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                for file in files:
                    if file.lower().endswith('.pdf'):
                        pdf_files.add(os.path.join(root, file))
    return list(pdf_files)

def handle(command, history, note_path, full_content):
    all_chat_text = ""
    for turn in history:
        for part in turn.get("parts", []):
            if isinstance(part, dict) and "text" in part:
                all_chat_text += part["text"] + "\n"
            elif not isinstance(part, dict) and hasattr(part, 'text') and getattr(part, 'text', None):
                all_chat_text += getattr(part, 'text') + "\n"
                
    # 提取所有存在的路径并找寻 PDF
    valid_paths = extract_existing_paths(all_chat_text)
    pdf_files = gather_pdfs_from_paths(valid_paths)
    
    # 防止加载过多 PDF
    if len(pdf_files) > 15:
        return f"❌ 找到的 PDF 文件过多 ({len(pdf_files)} 个)，为了避免超出大模型的上下文限制，请指定更精确的路径或单个文件。"
        
    uploaded_files = []
    if pdf_files:
        print(f"🔍 找到 {len(pdf_files)} 个相关 PDF，准备加载...")
        for pdf in pdf_files:
            file_info = get_or_upload_pdf(pdf)
            if file_info:
                uploaded_files.append(file_info)

    # 建立笔记上下文索引
    vault_index = utils.build_vault_index(utils.VAULT_DIR)
    linked_context, linked_images = utils.extract_linked_context(all_chat_text, vault_index)

    _, context_header, _, _ = utils.parse_document(full_content)
    final_system_prompt = SYSTEM_PROMPT_BASE
    
    if context_header.strip():
        clean_header = context_header.strip()
        if clean_header.endswith("---"):
            clean_header = clean_header[:-3].strip()
        if clean_header:
            final_system_prompt += f"\n\n请参考以下当前笔记的正文内容来辅助回答：\n{clean_header}"

    if linked_context:
        final_system_prompt += f"\n\n请参考以下我提到的相关笔记内容来辅助回答：\n{linked_context}"

    # 构建强类型的 typed_history
    typed_history = []
    for turn in history:
        typed_parts = []
        for p in turn.get("parts", []):
            if isinstance(p, dict) and "text" in p:
                typed_parts.append(types.Part.from_text(text=p["text"]))
            elif not isinstance(p, dict):
                typed_parts.append(p)
        typed_history.append(types.Content(role=turn["role"], parts=typed_parts))

    # 在最后一次提问的 parts 中追加 PDF 上下文
    if uploaded_files:
        pdf_parts = []
        for info in uploaded_files:
            pdf_parts.append(types.Part.from_text(text=f"--- 下面是名为 {info.get('original_name', '未知文档')} 的文档内容 ---"))
            pdf_parts.append(types.Part.from_uri(file_uri=info['uri'], mime_type=info['mime_type']))
            pdf_parts.append(types.Part.from_text(text=f"--- 文档结束 ---"))
            
        # 将 PDF parts 插在用户实际提问的前面
        typed_history[-1].parts = pdf_parts + typed_history[-1].parts

    # 追加提取到的图片
    for img_path in linked_images:
        try:
            with open(img_path, 'rb') as f:
                img_bytes = f.read()
                
            ext = img_path.suffix.lower()
            mime_type = "image/png"
            if ext in ['.jpg', '.jpeg']: mime_type = "image/jpeg"
            elif ext == '.webp': mime_type = "image/webp"
            elif ext == '.gif': mime_type = "image/gif"
            
            typed_history[-1].parts.append(
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
            )
            print(f"已将图片作为视觉上下文加载: {img_path.name}")
        except Exception as e:
            print(f"Error: 图片加载失败 {img_path}: {e}")

    try:
        print(f"正在调用 {utils.MODEL_NAME} 生成回复...")
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=typed_history,
            config=types.GenerateContentConfig(
                temperature=0.4,
                system_instruction=final_system_prompt,
            )
        )
        
        if not res.text:
            return "❌ API 返回了空内容。"
            
        # 给用户的回答中附加上载信息（如果是带了明确的新文件）
        reply = res.text
        if uploaded_files:
            file_names = [info.get('original_name', '未知') for info in uploaded_files]
            reply += f"\n\n*(📚 已挂载文档: {', '.join(file_names)})*"
            
        return reply
    except Exception as e:
        return f"❌ 发生错误: {e}"
