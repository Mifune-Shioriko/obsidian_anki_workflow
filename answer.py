import sys
import os
import re
import datetime
from pathlib import Path
from google import genai
from google.genai import types
from dotenv import load_dotenv # 加载环境变量

load_dotenv()

# --- 配置区域 ---
MODEL_NAME = "gemini-3-flash-preview" 
VAULT_DIR = os.getenv("VAULT_DIR")
DAILY_NOTES_PATH = os.path.join(VAULT_DIR, "Daily Notes")

# --- 环境变量读取 ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    print("Error: 未找到 GOOGLE_API_KEY，请检查 .env 文件是否配置正确。")
    sys.exit(1)

SYSTEM_PROMPT_BASE = """你现在的任务是回答我最后的问题。你接收到的是我先前的对话以及我的最后一个问题或者想法或者请求。
严格遵守以下输出格式限制：

1. 回答结构
- 每次回答仅允许：
  - 1～2 个自然段
  - 少数 bullet points
  - 可以使用表格

2. bullet points 格式
- 使用 "-" 作为 bullet
- 每行一个 bullet
示例：
- 第一条内容
- 第二条内容

3. 禁止格式
- 不允许使用 **粗体**
- 不允许使用超过 2 层结构
- 不允许使用长列表

4. 数学公式规则
- 行内公式使用 $...$
- 独立公式使用 $$...$$
- $ 或 $$ 与周围文本之间必须有空格
- 行内公式 $ 必须紧贴公式本体

正确示例：
牛顿第二定律 $F=ma$ 描述了力与加速度的关系。

错误示例：
牛顿第二定律$F=ma$描述了力与加速度的关系。
"""

# ... (保留原有的 get_todays_note_path, split_note_content, parse_markdown_to_history 函数) ...
def get_todays_note_path():
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    return os.path.join(DAILY_NOTES_PATH, f"{today_str}.md")

def split_note_content(file_content):
    header_idx = file_content.find('## Today')
    if header_idx == -1:
        return "", file_content
    match = re.search(r'^---\s*$', file_content[header_idx:], re.MULTILINE)
    if match:
        split_point = header_idx + match.end()
        return file_content[:split_point], file_content[split_point:]
    else:
        print("Warning: 在 '## Today' 下方没有找到 '---' 分割线。")
        sys.exit(1)

def parse_markdown_to_history(chat_content):
    lines = chat_content.strip().split('\n')
    history = []
    current_role = None
    current_text = []

    def save_turn():
        if current_role and current_text:
            text_str = '\n'.join(current_text).strip()
            if text_str:
                history.append({
                    "role": current_role,
                    "parts": [{"text": text_str}]
                })
            current_text.clear()

    for line in lines:
        is_user = line.startswith('> ') or line == '>'

        if is_user:
            if current_role == "model":
                save_turn()
            current_role = "user"
            current_text.append(line[2:] if line.startswith('> ') else "")
        else:
            if current_role == "user":
                save_turn()
            current_role = "model"
            current_text.append(line)

    save_turn()
    return history

def extract_linked_context(text):
    """提取文本中的双链，区分文本笔记和图片"""
    # 匹配 [[笔记名]]、[[笔记名|别名]] 或 ![[图片名]]
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    links = re.findall(pattern, text)
    
    context_str = ""
    image_paths = []
    
    if not links:
        return context_str, image_paths
        
    vault_path = Path(VAULT_DIR)
    image_extensions = ['.png', '.jpg', '.jpeg', '.webp', '.gif']
    
    for link in set(links):
        link = link.strip()
        ext = os.path.splitext(link)[1].lower()
        
        # 判断是不是图片文件
        if ext in image_extensions:
            found_files = list(vault_path.rglob(link))
            found_files = [p for p in found_files if ".obsidian" not in str(p) and ".trash" not in str(p)]
            if found_files:
                image_paths.append(found_files[0])
                print(f"已成功捕获图片链接: {link}")
            else:
                print(f"Warning: 库中未找到引用的图片 -> {link}")
                
        # 否则当作 Markdown 笔记处理
        else:
            filename = f"{link}.md"
            found_files = list(vault_path.rglob(filename))
            found_files = [p for p in found_files if ".obsidian" not in str(p) and ".trash" not in str(p)]
            
            if found_files:
                try:
                    with open(found_files[0], 'r', encoding='utf-8') as f:
                        content = f.read()
                        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
                        context_str += f"\n[参考笔记《{link}》的正文内容]:\n{body[:2000]}\n"
                        print(f"已成功加载参考笔记: {link}")
                except Exception as e:
                    print(f"Warning: 无法读取笔记 {link}: {e}")
            else:
                print(f"Warning: 库中未找到引用的笔记 -> {link}")
                
    return context_str, image_paths

def sanitize_format(text):
    # 去粗体
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    
    # 统一 bullet：将行首的 * 或 + 替换为 -，并保留原有的缩进 (空格)
    text = re.sub(r"^(\s*)[\*\+]\s+", r"\1- ", text, flags=re.MULTILINE)
    
    # 修复 bullet 空格：修复诸如 "-   " 多个空格的情况，同样保留行首缩进
    text = re.sub(r"^(\s*)-\s+", r"\1- ", text, flags=re.MULTILINE)
    
    # 修复数字列表
    text = re.sub(r"^(\s*)(\d+)\.\s*(.+)", r"\1\2. \3", text, flags=re.MULTILINE)
    
    lines = text.splitlines()
    fixed_lines = []
    
    # 【修复重点】：定义一个包含行首允许空白的列表匹配正则
    list_pattern = r"^\s*(- |\d+\.\s)"
    
    for i, line in enumerate(lines):
        # 使用新的正则进行匹配
        is_current_list = bool(re.match(list_pattern, line))
        is_empty = (line.strip() == "")
        
        # 核心逻辑：精准消灭列表项之间的空行
        if is_empty:
            # 1. 检查上一行是不是列表项
            prev_is_list = bool(fixed_lines and re.match(list_pattern, fixed_lines[-1]))
            
            # 2. 检查下一个非空行是不是列表项
            next_is_list = False
            for j in range(i + 1, len(lines)):
                if lines[j].strip() != "":
                    next_is_list = bool(re.match(list_pattern, lines[j]))
                    break
            
            # 如果这行空格夹在两个列表项中间，直接抛弃
            if prev_is_list and next_is_list:
                continue
                
        # 边缘保护：如果当前是列表项，且紧挨着上方的普通正文，补一个空行防止 Markdown 渲染粘连
        if is_current_list:
            if fixed_lines and fixed_lines[-1].strip() != "" and not bool(re.match(list_pattern, fixed_lines[-1])):
                fixed_lines.append("")
                
        fixed_lines.append(line)
        
    return "\n".join(fixed_lines).strip()

def append_ai_response(filepath, response_text):
    with open(filepath, 'a', encoding='utf-8') as f:
        f.write("\n\n" + response_text.strip() )

def main():
    note_path = get_todays_note_path()
    if not os.path.exists(note_path):
        print(f"Error: 找不到今天的笔记文件 -> {note_path}")
        sys.exit(1)

    with open(note_path, 'r', encoding='utf-8-sig') as f:
        full_content = f.read()

    print("正在定位分割线并提取对话历史...")
    _, chat_content = split_note_content(full_content)
    if not chat_content.strip():
        sys.exit(1)

    history = parse_markdown_to_history(chat_content)
    if not history or history[-1]["role"] == "model":
        sys.exit(1)

    client = genai.Client(api_key=GOOGLE_API_KEY)

    # 1. 解析文本双链和图片双链
    linked_context, linked_images = extract_linked_context(chat_content)
    
    # 2. 组装 System Prompt
    final_system_prompt = SYSTEM_PROMPT_BASE
    if linked_context:
        final_system_prompt += f"\n\n请参考以下我提到的笔记内容来辅助回答：\n{linked_context}"

    # 3. 将图片转换为二进制 Part，追加到 user 的最后一次发言中
    for img_path in linked_images:
        try:
            with open(img_path, 'rb') as f:
                img_bytes = f.read()
                
            ext = img_path.suffix.lower()
            mime_type = "image/png"
            if ext in ['.jpg', '.jpeg']: mime_type = "image/jpeg"
            elif ext == '.webp': mime_type = "image/webp"
            
            # 使用新版 SDK 的 Part 格式加载二进制数据
            history[-1]["parts"].append(
                types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
            )
            print(f"已将图片作为视觉上下文加载给 AI: {img_path.name}")
        except Exception as e:
            print(f"Error: 图片加载失败 {img_path}: {e}")

    print(f"正在调用 {MODEL_NAME} 生成回复...")
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=history,
            config=types.GenerateContentConfig(
                temperature=0.7,
                system_instruction=final_system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                thinking_config=types.ThinkingConfig(thinking_level='medium')
            )
        )
        
        reply_text = sanitize_format(response.text)
        if reply_text:
            append_ai_response(note_path, reply_text)
            print("Success! AI 回复已成功追加到今天笔记的末尾。")
        else:
            print("Warning: AI 返回了空内容。")
            
    except Exception as e:
        print(f"Google Gen AI Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
