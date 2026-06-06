import os
import re
from pathlib import Path
import requests
from dotenv import load_dotenv

# ================= 1. 环境变量与全局配置 =================
# 优先读取当前目录的 .env
current_dir = Path(__file__).resolve().parent
env_path = current_dir / '.env'
if not env_path.exists():
    env_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=env_path)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
VAULT_DIR = os.getenv("VAULT_DIR")
ANKI_URL = os.getenv("ANKI_URL", "http://127.0.0.1:8765")
MODEL_NAME = "gemini-3.5-flash"

if not GOOGLE_API_KEY:
    print(f"Error: 未能在 {env_path} 找到 GOOGLE_API_KEY，请检查配置。")
    import sys
    sys.exit(1)

# ================= 2. 基础文档与历史解析 =================
def parse_document(file_content):
    # 1. 抽离底部的卡片区域
    match = re.search(r'^## 卡片\s*$', file_content, re.MULTILINE)
    if match:
        cards_idx = match.start()
        main_body = file_content[:cards_idx]
        cards_body = file_content[cards_idx:]
    else:
        main_body = file_content
        cards_body = ""
        
    # 2. 抽离相关笔记区域
    related_match = re.search(r'^##\s*(?:相关笔记|Related\s+Notes)\s*$', main_body, re.MULTILINE | re.IGNORECASE)
    if related_match:
        related_idx = related_match.start()
        related_body = main_body[related_idx:]
        main_body = main_body[:related_idx]
    else:
        related_body = ""
        
    # 3. 寻找真正的对话起始点
    # 规则：笔记中的对话一定是以 `> ` 开始的段落
    # 如果找不到 `> `，说明全是笔记正文，没有聊天记录
    # 如果找到了，我们需要寻找这个 `> ` 之前可能存在的 `---` 作为正式的分割点
    
    chat_start_idx = -1
    context_start_idx = 0 # 新增：用于记录发送给 AI 的上下文应该从哪里开始
    first_user_quote_match = re.search(r'^>\s', main_body, re.MULTILINE)
    
    if first_user_quote_match:
        # 找到了用户的提问 `> `，这说明聊天记录至少从这里开始
        quote_idx = first_user_quote_match.start()
        
        # 往回找，看看这句 `> ` 之前最近的一个 `---` 在哪里
        # 规定：这个 `---` 必须出现在 YAML 头部之后（如果有 YAML 头部的话）
        
        # 先找到第一对 YAML 标识（如果有的话）
        yaml_end_idx = 0
        if main_body.startswith('---\n'):
            second_dash = main_body.find('\n---\n', 4)
            if second_dash != -1:
                yaml_end_idx = second_dash + 5 # 指向第二个 --- 之后的字符
        
        # 在 yaml_end_idx 和 quote_idx 之间寻找最后一个 `---`
        search_area = main_body[yaml_end_idx:quote_idx]
        last_dash_in_area = search_area.rfind('\n---\n')
        
        if last_dash_in_area != -1:
            # 如果找到了 `---`，就在这里截断聊天记录
            chat_start_idx = yaml_end_idx + last_dash_in_area + 5
            # 同时，由于存在 `---`，发送给 AI 的正文上下文也将从这里开始截断
            # 意味着 `---` 以上的内容将被省略，达到节省 token 的目的
            context_start_idx = chat_start_idx
        else:
            # 如果没找到 `---`，说明用户直接在正文下面追加了 `> ` 提问
            # 此时以第一个 `> ` 作为聊天记录的起点
            chat_start_idx = quote_idx
            # 因为没有 `---` 分隔，我们将从 YAML 结束的地方（或者文件开头）提取全部正文作为上下文
            context_start_idx = yaml_end_idx
            
    if chat_start_idx != -1:
        full_header = main_body[:chat_start_idx]
        context_header = main_body[context_start_idx:chat_start_idx]
        chat_content = main_body[chat_start_idx:]
    else:
        # 如果找不到 `>` 提问，说明这是一篇普通的纯文本或由程序直接生成的笔记
        # 我们根据 YAML 的边界来分离 header 和正文
        if main_body.startswith('---\n'):
            second_dash = main_body.find('\n---\n', 4)
            if second_dash != -1:
                # 找到第二个 `---`，紧随其后的就是正文
                yaml_end_idx = second_dash + 5
                full_header = main_body[:yaml_end_idx]
                context_header = "" # 纯文本模式下，如果没有对话，context_header 没有实际意义
                chat_content = main_body[yaml_end_idx:]
            else:
                # 只有一对 `---` 的情况（理论上属于格式错误，但做个兜底）
                full_header = ""
                context_header = ""
                chat_content = main_body
        else:
            full_header = ""
            context_header = ""
            chat_content = main_body
            
    # 过滤掉 context_header 中可能存在的相关笔记，防止污染 AI 问答上下文
    if context_header:
        related_match = re.search(r'^##\s*(?:相关笔记|Related\s+Notes)\s*$', context_header, re.MULTILINE | re.IGNORECASE)
        if related_match:
            context_header = context_header[:related_match.start()].strip()
            
    return full_header, context_header, chat_content, related_body, cards_body

def parse_markdown_to_history(chat_content):
    lines = chat_content.strip().split('\n')
    history = []
    current_role, current_text = None, []

    def save_turn():
        if current_role and current_text:
            text_str = '\n'.join(current_text).strip()
            if text_str:
                history.append({"role": current_role, "parts": [{"text": text_str}]})
            current_text.clear()

    for line in lines:
        is_user = line.startswith('> ') or line == '>'
        if is_user:
            if current_role == "model": save_turn()
            current_role = "user"
            clean_line = line[2:] if line.startswith('> ') else (line[1:] if line.startswith('>') else line)
            current_text.append(clean_line)
        else:
            if current_role == "user": save_turn()
            current_role = "model"
            # 不要丢弃任何原本的非 > 行的内容，它们都是大模型的回答
            current_text.append(line)

    save_turn()
    return history

def unbold_text(text):
    """
    Strips bold markers (** and __) and cleans up surrounding spaces when adjacent to CJK/punctuation characters.
    """
    def is_cjk_or_punct(ch):
        if not ch:
            return False
        o = ord(ch)
        return (0x4e00 <= o <= 0x9fff) or (0x3000 <= o <= 0x303f) or (0xff00 <= o <= 0xffef)

    def replace_bold(match):
        leading_space = match.group(1)
        content = match.group(3)
        trailing_space = match.group(4)
        
        start_idx = match.start()
        end_idx = match.end()
        full_str = match.string
        
        char_before = full_str[start_idx - 1] if start_idx > 0 else ""
        char_after = full_str[end_idx] if end_idx < len(full_str) else ""
        
        if leading_space:
            if is_cjk_or_punct(char_before) or (content and is_cjk_or_punct(content[0])):
                leading_space = ""
        if trailing_space:
            if (content and is_cjk_or_punct(content[-1])) or is_cjk_or_punct(char_after):
                trailing_space = ""
                
        return f"{leading_space}{content}{trailing_space}"

    return re.sub(r"(\s*)(\*\*|__)(.*?)\2(\s*)", replace_bold, text)

def format_cjk_spacing(text):
    # Split to protect inline code, double links, formulas, markdown links
    parts = re.split(r"(```.*?```|`[^`\n]*`|\[\[.*?\]\]|\$\$[^$]*?\$\$|\$[^$\n]*?\$|\[.*?\]\(.*?\))", text, flags=re.DOTALL)
    for i in range(len(parts)):
        if i % 2 == 0 and parts[i].strip():
            segment = parts[i]
            # 中文与英文/数字之间加空格
            segment = re.sub(r"([\u4e00-\u9fff])([a-zA-Z0-9])", r"\1 \2", segment)
            segment = re.sub(r"([a-zA-Z0-9])([\u4e00-\u9fff])", r"\1 \2", segment)
            # 全角标点与中/英/数字之间去掉空格
            punct_class = r"[\u3000-\u303f\uff00-\uffef\u201c\u201d\u2018\u2019\u2014\u2026]"
            segment = re.sub(r"([a-zA-Z0-9\u4e00-\u9fff])[ \t]+(" + punct_class + ")", r"\1\2", segment)
            segment = re.sub(r"(" + punct_class + ")[ \t]+([a-zA-Z0-9\u4e00-\u9fff])", r"\1\2", segment)
            parts[i] = segment
    return "".join(parts)

def sanitize_format(text):
    parts = re.split(r"(```.*?```)", text, flags=re.DOTALL)
    for i in range(len(parts)):
        if i % 2 == 0 and parts[i].strip():
            segment = parts[i]
            segment = re.sub(r"(?<!\$)\$[ \t]*([^$\n]+?)[ \t]*\$(?!\$)", r"$\1$", segment)
            segment = re.sub(r"(?<!\$)\$\$[ \t]*(.*?)[ \t]*\$\$(?!\$)", r"$$\1$$", segment, flags=re.DOTALL)
            segment = re.sub(r"\n*^[ \t]*([-*_])[ \t]*(?:\1[ \t]*){2,}\s*$\n*", "\n\n", segment, flags=re.MULTILINE)
            segment = unbold_text(segment)
            segment = re.sub(r" \[\d+\]", "", segment)
            segment = format_cjk_spacing(segment)
            segment = re.sub(r"^([ \t]*)[\*\+]\s+", r"\1- ", segment, flags=re.MULTILINE)
            segment = re.sub(r"^([ \t]*)-(?![-\s])(.+)$", r"\1- \2", segment, flags=re.MULTILINE)
            segment = re.sub(r"^([ \t]*)(\d+)\.\s*(.+)", r"\1\2. \3", segment, flags=re.MULTILINE)
            
            lines = segment.splitlines()
            fixed_lines = []
            list_pattern = r"^[ \t]*(- |\d+\.\s)"
            bullet_indent_stack = []
            
            for j, line in enumerate(lines):
                is_current_list = bool(re.match(list_pattern, line))
                is_empty = (line.strip() == "")
                
                if is_empty:
                    prev_is_list = bool(fixed_lines and re.match(list_pattern, fixed_lines[-1]))
                    next_is_list = False
                    for k in range(j + 1, len(lines)):
                        if lines[k].strip() != "":
                            next_is_list = bool(re.match(list_pattern, lines[k]))
                            break
                    if prev_is_list and next_is_list:
                        continue
                        
                if is_current_list:
                    if fixed_lines and fixed_lines[-1].strip() != "" and not bool(re.match(list_pattern, fixed_lines[-1])):
                        fixed_lines.append("")
                
                bullet_match = re.match(r"^([ \t]*)-\s+(.*)", line)
                if bullet_match:
                    raw_indent = bullet_match.group(1)
                    orig_indent_val = raw_indent.count(' ') + raw_indent.count('\t') * 4
                    content = bullet_match.group(2)
                    
                    if not bullet_indent_stack:
                        bullet_indent_stack = [orig_indent_val]
                        level = 0
                    else:
                        if orig_indent_val > bullet_indent_stack[-1]:
                            bullet_indent_stack.append(orig_indent_val)
                            level = len(bullet_indent_stack) - 1
                        else:
                            while bullet_indent_stack and bullet_indent_stack[-1] > orig_indent_val:
                                bullet_indent_stack.pop()
                            if not bullet_indent_stack:
                                bullet_indent_stack.append(orig_indent_val)
                            level = len(bullet_indent_stack) - 1
                            
                    new_indent = "\t" * level
                    line = f"{new_indent}- {content}"
                    
                elif not is_empty and not is_current_list:
                    bullet_indent_stack = []
                        
                fixed_lines.append(line)
            parts[i] = "\n".join(fixed_lines)
            
    return "".join(parts).strip()

def insert_ai_response(filepath, header, chat_content, ai_response, related_body, cards_body):
    chat_content = chat_content.rstrip()
    ai_response = ai_response.strip()
    new_content = f"{header}{chat_content}\n\n{ai_response}\n\n"
    if related_body:
        new_content += f"{related_body.strip()}\n\n"
    if cards_body:
        new_content += f"{cards_body.lstrip()}"
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

# ================= 3. Anki API 与卡片处理 =================
def anki_request(action, params=None):
    payload = {"action": action, "version": 6, "params": params or {}}
    try:
        res = requests.post(ANKI_URL, json=payload).json()
        if res.get('error'): return None
        return res.get('result')
    except:
        return None

def get_current_anki_context():
    current_card = anki_request("guiCurrentCard")
    if not current_card or not current_card.get("cardId"): return None
    cards_info = anki_request("cardsInfo", {"cards": [current_card.get("cardId")]})
    if not cards_info: return None
    return str(cards_info[0].get("note"))

def parse_markdown_table(content):
    match = re.search(r'^## 卡片\s*$', content, re.MULTILINE)
    if not match: return []
    table_text = content[match.end():]
    return extract_cards_from_table_text(table_text)

def extract_cards_from_table_text(text):
    cards = []
    lines = text.strip().split('\n')
    in_table = False
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line.startswith('|'):
            if in_table: break  # 离开表格区域
            continue
            
        # 切片并去除每个单元格两端的空格
        parts = [p.strip() for p in stripped_line.split('|')]
        
        # 宽容匹配表头：只要列名对得上，不管中间加了多少空格对齐都能识别
        if len(parts) >= 4 and parts[1] == '问题' and parts[2] == '答案' and parts[3] in ['Anki ID', 'ID']:
            in_table = True
            continue
            
        # 跳过分隔线（比如 |---|---|---|）
        if in_table and '---' in line:
            continue
            
        # 解析真实数据
        if in_table and len(parts) >= 4:
            q = parts[1].replace('\\|', '|').replace('<br>', '\n')
            a = parts[2].replace('\\|', '|').replace('<br>', '\n')
            nid = parts[3]
            # 过滤掉空行
            if q or a or nid: 
                cards.append({"question": q, "answer": a, "id": nid})
                
    return cards

def format_cards_to_table(cards):
    table = "| 问题 | 答案 | Anki ID |\n| ---- | ---- | ------- |\n"
    for c in cards:
        q = (c.get('question', '') if isinstance(c, dict) else c.question).replace('\n', '<br>').replace('|', '\\|')
        a = (c.get('answer', '') if isinstance(c, dict) else c.answer).replace('\n', '<br>').replace('|', '\\|')
        uid = c.get('id', '') if isinstance(c, dict) else c.id
        table += f"| {q} | {a} | {uid} |\n"
    return table.strip()

def rewrite_markdown_table(file_path, original_content, updated_cards):
    new_table_block = "## 卡片\n\n" + format_cards_to_table(updated_cards) + "\n"
    match = re.search(r'^## 卡片\s*$', original_content, re.MULTILINE)
    if match:
        base_content = original_content[:match.start()].rstrip()
    else:
        base_content = original_content.rstrip()
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(base_content + "\n\n" + new_table_block)

# ================= 4. Vault 索引与上下文提取 =================
def build_vault_index(vault_dir):
    """
    预先构建 Obsidian 库的文件索引，跳过隐藏文件夹，避免重复的 rglob 扫描。
    返回结构: {'文件名.md': Path对象, '图片.png': Path对象, ...}
    """
    index = {}
    if not vault_dir: return index
    vault_path = Path(vault_dir).expanduser()
    
    for root, dirs, files in os.walk(vault_path):
        # 实时修改 dirs 列表，直接阻止 os.walk 进入这些耗时的隐藏目录
        dirs[:] = [d for d in dirs if d not in ['.obsidian', '.trash', '.git']]
        
        for file in files:
            # 存储格式：如果文件叫 test.md，字典的 key 就是 'test.md'
            index[file.lower()] = Path(root) / file
            
    return index

def extract_linked_context(text, vault_index):
    """
    提取文本中所有的 [[双链]]，并根据 vault_index 找到对应文件
    返回提取到的文字上下文和图片路径列表
    """
    pattern = r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    links = re.findall(pattern, text)
    
    context_str = ""
    image_paths = []
    
    if not links:
        return context_str, image_paths
        
    image_extensions = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    
    for link in set(links):
        link_clean = link.strip()
        
        # 忽略文件夹路径，只提取文件名
        filename = link_clean.split('/')[-1]
        ext = os.path.splitext(filename)[1].lower()
        
        # 统一转换为小写进行匹配，处理 Obsidian 中大小写不敏感的链接
        if ext in image_extensions:
            search_key = filename.lower()
        else:
            search_key = f"{filename}.md".lower()
            
        target_path = vault_index.get(search_key)
        
        if target_path:
            if ext in image_extensions:
                image_paths.append(target_path)
                print(f"已成功捕获图片链接: {link_clean}")
            else:
                try:
                    with open(target_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        body = re.sub(r'^---\s*\n.*?\n---\s*\n', '', content, flags=re.DOTALL)
                        context_str += f"\n[参考笔记《{link_clean}》的正文内容]:\n{body[:2000]}\n"
                        print(f"已成功加载参考笔记: {link_clean}")
                except Exception as e:
                    print(f"Warning: 无法读取笔记 {link_clean}: {e}")
        else:
            print(f"Warning: 库中未找到引用的资源 -> {link_clean}")
                
    return context_str, image_paths
