import os
import re
import json
import uuid
import sys
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ==========================================
# 1. 基础与环境配置
# ==========================================
# 加载 .env 文件中的环境变量
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    print("[!] Error: 未找到 GOOGLE_API_KEY，请检查 .env 文件或环境变量配置。")
    sys.exit(1)

BASE_DIR = os.getenv("VAULT_DIR", r"C:\Default\Path\If\Needed")
TODAY_STR = datetime.now().strftime("%Y-%m-%d")
MODEL_NAME = "gemini-2.5-flash" 

def main():
    daily_dir = os.path.join(BASE_DIR, "Daily Notes")
    daily_path = os.path.join(daily_dir, f"{TODAY_STR}.md")

    print(f"[*] 正在检查当日日记: {daily_path}")

    # ==========================================
    # 2. 读取文件
    # ==========================================
    if not os.path.exists(daily_path):
        print(f"[!] 找不到文件，请检查路径是否正确: {daily_path}")
        return

    with open(daily_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # ==========================================
    # 3. 解析与分割
    # ==========================================
    separator_regex = re.compile(r'\r?\n-{3,}[ \t]*\r?\n')
    matches = list(separator_regex.finditer(content))
    
    has_draft = False
    pre_content = content
    draft_content = ""

    if matches:
        last_match = matches[-1]
        last_match_index = last_match.start()
        match_length = len(last_match.group())

        pre_content = content[:last_match_index]
        draft_content = content[last_match_index + match_length:].strip()
        
        if len(draft_content) > 0:
            has_draft = True
    else:
        eof_regex = re.compile(r'\r?\n-{3,}[ \t]*$')
        eof_match = eof_regex.search(content)
        if eof_match:
            pre_content = content[:eof_match.start()]
            draft_content = content[eof_match.start() + len(eof_match.group()):].strip()
            if len(draft_content) > 0:
                has_draft = True

    if not has_draft:
        print("[-] 未在文件末尾检测到有效草稿内容，流程结束。")
        return

    print(f"[*] 成功提取草稿内容，长度: {len(draft_content)} 字符")

    # ==========================================
    # 4. 调用 Google GenAI SDK
    # ==========================================
    print(f"[*] 正在请求 {MODEL_NAME} 生成标题...")
    
    prompt = f"""你是一个极其专业的个人知识库（PKM / Obsidian）整理大师。你的任务是阅读给定的笔记草稿片段，为其提炼一个最适合作为 Obsidian 卡片盒原子笔记（Atomic Note）文件名的中文标题。

【标题提炼黄金准则】：
1. 极度精炼与具象：字数通常控制在 2-6 个词/汉字最佳。坚决拒绝宽泛、冗余、无实际意义的词汇（如“学习心得”、“笔记整理”、“知识点总结”、“未命名片段”）。
2. 直击核心概念：直接使用笔记中最核心的专业术语、机制名称、公式或核心概念作为标题（例如：`React 状态批处理`、`Anki FSRS 算法优势`、`工作记忆的特性`）。
3. 杜绝修饰性前缀：严禁出现“关于...”、“简析...”、“浅谈...”、“简述...”等任何修饰、过渡性字眼。
4. 中英文混排空格规范：若标题中包含中英文混用，中文与英文字母/单词之间必须使用单个空格隔开（例如：`论述 carnivore diet 的局限性` 是正确的排版形式，绝不要写成 `论述carnivore diet的局限性`）。
5. 保证文件名合法：仅限汉字、英文字母、数字和空格，绝对禁止包含任何标点符号（如冒号、逗号、问号、斜杠等）。

笔记内容：
{draft_content}

请严格仅以 JSON 格式输出：
{{
    "title": "卡片盒笔记标题"
}}"""

    client = genai.Client(api_key=GOOGLE_API_KEY)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                response_mime_type="application/json", # 强制返回纯净 JSON 格式
            )
        )
        ai_response_text = response.text
    except Exception as e:
        print(f"[!] API 请求失败: {e}")
        return

    # ==========================================
    # 5. 清洗数据与生成文件
    # ==========================================
    try:
        ai_data = json.loads(ai_response_text)
        raw_title = ai_data.get("title", f"未命名片段_{int(datetime.now().timestamp())}")
    except json.JSONDecodeError:
        print("[!] JSON 解析失败，使用兜底标题。")
        raw_title = f"未命名片段_{int(datetime.now().timestamp())}"

    # 替换非法字符，确保可以作为合法的文件名
    safe_title = re.sub(r'[:/\\?*|"<>;]', ' - ', raw_title).strip()
    if not safe_title:
        safe_title = "Untitled"

    print(f"[*] AI 生成标题: {safe_title}")

    atomic_dir = os.path.join(BASE_DIR, "Atomic Notes")
    atomic_path = os.path.join(atomic_dir, f"{safe_title}.md")
    note_id = uuid.uuid4().hex
    
    # 自动清洗 draft_content，剔除幻灯片原件文字、图片链接以及对话触发命令
    lines = draft_content.splitlines()
    has_agent_mention = any(re.search(r'@\w+', l) for l in lines if l.strip().startswith('>'))
    
    if has_agent_mention:
        last_quote_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('>'):
                last_quote_idx = i
                
        if last_quote_idx != -1:
            # 只保留最后一个 > 之后的行，并去除多余的首尾空行
            remaining_lines = lines[last_quote_idx + 1:]
            start_idx = 0
            while start_idx < len(remaining_lines) and not remaining_lines[start_idx].strip():
                start_idx += 1
            cleaned_draft_content = "\n".join(remaining_lines[start_idx:])
        else:
            cleaned_draft_content = draft_content
    else:
        cleaned_draft_content = draft_content
    
    atomic_content = f"""---
date: {TODAY_STR}
title: {safe_title}
id: {note_id}
type: from_daily_notes
---
{cleaned_draft_content}
"""

    updated_daily_content = f"{pre_content.strip()}\n[[{safe_title}]]\n\n---\n\n"

    # ==========================================
    # 6. 写入硬盘
    # ==========================================
    try:
        if not os.path.exists(atomic_dir):
            os.makedirs(atomic_dir)

        with open(atomic_path, 'w', encoding='utf-8') as f:
            f.write(atomic_content)
        
        with open(daily_path, 'w', encoding='utf-8') as f:
            f.write(updated_daily_content)

        print("[+] 成功！")
        print(f"    - 已生成新卡片: {atomic_path}")
        print(f"    - 已更新当日日记: {daily_path}")

    except Exception as e:
        print(f"[!] 写入文件失败: {e}")

if __name__ == "__main__":
    main()
