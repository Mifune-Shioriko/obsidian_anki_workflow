import sys
import json
import requests
import os
import re
import markdown
from dotenv import load_dotenv
from urllib.parse import quote

load_dotenv()

# ================= 配置区域 =================
VAULT_ROOT = os.getenv("VAULT_DIR")
TARGET_FOLDER_NAME = "Atomic Notes"
TARGET_FULL_PATH = os.path.join(VAULT_ROOT, TARGET_FOLDER_NAME)
FILES_DIR = os.path.join(VAULT_ROOT, "Files")

ANKI_URL = os.getenv("ANKI_URL", "http://127.0.0.1:8765")
SEARCH_FIELD = "Advanced URI"
UPDATE_FIELD = "原来的笔记"

DECK_NAME = os.getenv("ANKI_DECK_NAME", "Obsidian")
NOTE_TYPE = os.getenv("ANKI_NOTE_TYPE", "Obsidian")
VAULT_NAME = os.getenv("OBSIDIAN_VAULT_NAME", "my_obsidian_notes")
# ===========================================

# --- 基础工具函数 ---
def load_file(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return f.read()
    except:
        return None

def extract_id_from_yaml(content):
    pattern = r'^id:\s*["\']?([^"\s\n\']+)["\']?'
    match = re.search(pattern, content, re.MULTILINE)
    return match.group(1).strip() if match else None

def obsidian_to_anki_math(text):
    if not text: return text
    text = re.sub(r'\$\$(.*?)\$\$', r'\\[\1\\]', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.*?)(?<!\\)\$', r'\\(\1\\)', text, flags=re.DOTALL)
    return text

# ================= 图片处理函数 =================
def process_media_links(text, add_spacing=False):
    """
    将 Obsidian 的 [[image.png]] 转换为 Anki 的 <img> 标签。
    add_spacing: 是否在图片前后强制增加换行（用于表格里的问题和答案字段）
    """
    if not text: return text

    # 核心修改：允许拓展名后面存在 | 及其跟随的缩放参数
    pattern = r'(!?)\[\[([^\]|]+\.(?:png|jpe?g|gif|svg|webp|bmp))(?:\|[^\]]+)?\]\]'
    
    def replace_match(match):
        prefix = match.group(1)
        filename = match.group(2).strip()
        file_path = os.path.join(FILES_DIR, filename)
        
        if os.path.exists(file_path):
            try:
                invoke("storeMediaFile", filename=filename, path=file_path)
                print(f"[媒体] 成功同步图片: {filename}")
            except Exception as e:
                print(f"[警告] 同步图片 {filename} 失败: {e}")
            
            # 如果开启了空行排版，前后各加两个 <br> 实现“空一行”的效果
            if add_spacing:
                return f'<br><br><img src="{filename}" alt="{prefix}"><br><br>'
            else:
                return f'<img src="{filename}" alt="{prefix}">'
        else:
            print(f"[警告] 找不到图片文件: {file_path}")
            return match.group(0) 
            
    return re.sub(pattern, replace_match, text, flags=re.IGNORECASE)

# =====================================================

def invoke(action, **params):
    response = requests.post(ANKI_URL, json={
        "action": action, "version": 6, "params": params
    }).json()
    if 'error' not in response:
        raise Exception('response is missing required error field')
    if 'result' not in response:
        raise Exception('response is missing required result field')
    if response['error'] is not None:
        raise Exception(response['error'])
    return response['result']

# --- 解析与转换函数 ---

def convert_to_html(text):
    if not text: return ""
    if "## 卡片" in text:
        text = text.split("## 卡片")[0].strip()
        
    text = re.sub(r'^\s*---\n.*?\n---\n', '', text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r'\$\$(.+?)\$\$', r'\\\\[\1\\\\]', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.+?)(?<!\\)\$', r'\\\\(\1\\\\)', text)
    
    # 提前处理媒体链接（主笔记文本原本就有良好的 Markdown 段落机制，所以 add_spacing=False 即可）
    text = process_media_links(text, add_spacing=False)
    
    try:
        return markdown.markdown(text, extensions=['extra', 'nl2br', 'codehilite'])
    except:
        return f"<pre>{text}</pre>"

def convert_qa_to_html(text):
    """
    专门用于处理卡片问题和答案的 HTML 转换函数
    包含公式保护、图片处理以及完整的 Markdown 渲染
    """
    if not text: return ""
    
    # 1. 双重转义公式，防止被 markdown 引擎吞掉斜杠
    text = re.sub(r'\$\$(.*?)\$\$', r'\\\\[\1\\\\]', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.*?)(?<!\\)\$', r'\\\\(\1\\\\)', text, flags=re.DOTALL)
    
    # 2. 处理图片（保留原有的前后加空行逻辑）
    text = process_media_links(text, add_spacing=True)
    
    # 3. 转换为 HTML
    try:
        return markdown.markdown(text, extensions=['extra', 'nl2br', 'codehilite'])
    except:
        return f"<pre>{text}</pre>"

def parse_markdown_table(content):
    if "## 卡片" not in content:
        return []
    
    table_text = content.split("## 卡片")[1]
    cards = []
    
    for line in table_text.strip().split('\n'):
        if not line.strip().startswith('|'): continue
        if '----' in line: continue
        if 'Anki ID' in line: continue 
        
        parts = [p.strip() for p in line.split('|')[1:-1]]
        if len(parts) >= 2:
            q = parts[0].replace('\\|', '|').replace('<br>', '\n')
            a = parts[1].replace('\\|', '|').replace('<br>', '\n')
            nid = parts[2] if len(parts) > 2 and parts[2].strip() else None
            cards.append({"question": q, "answer": a, "id": nid})
            
    return cards

def rewrite_markdown_table(file_path, original_content, updated_cards):
    table_header = "\n| 问题 | 答案 | Anki ID |\n| ---- | ---- | ------- |\n"
    new_rows = ""
    for card in updated_cards:
        q = card['question'].replace('\n', '<br>').replace('|', '\\|')
        a = card['answer'].replace('\n', '<br>').replace('|', '\\|')
        nid = card['id'] if card['id'] else ""
        new_rows += f"| {q} | {a} | {nid} |\n"
        
    new_table_block = "## 卡片\n" + table_header + new_rows
    
    if "## 卡片" in original_content:
        base_content = original_content.split("## 卡片")[0].rstrip()
    else:
        base_content = original_content.rstrip()
        
    final_content = base_content + "\n\n" + new_table_block
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(final_content)

# --- 核心同步引擎 ---

def sync_notes():
    if not os.path.exists(TARGET_FULL_PATH):
        print(f"错误：找不到文件夹 {TARGET_FULL_PATH}")
        return

    print(f"正在扫描文件夹: {TARGET_FOLDER_NAME} ...")
    
    for root, dirs, files in os.walk(TARGET_FULL_PATH):
        for file in files:
            if not file.endswith(".md"): continue
            
            file_path = os.path.join(root, file)
            content = load_file(file_path)
            note_id = extract_id_from_yaml(content)
            
            if not note_id: continue

            print(f"\n--- 处理笔记: {file} ---")
            source_uri = f"obsidian://advanced-uri?vault={quote(VAULT_NAME)}&uid={quote(note_id)}"
            new_html_context = convert_to_html(content)

            if "## 卡片" not in content:
                continue

            obsidian_cards = parse_markdown_table(content)
            
            query = f'"{SEARCH_FIELD}:*{note_id}*"'
            anki_ids = invoke("findNotes", query=query)
            
            if not anki_ids and not obsidian_cards:
                continue

            obsidian_known_ids = [int(c['id']) for c in obsidian_cards if c['id']]
            
            ids_to_delete = [i for i in anki_ids if i not in obsidian_known_ids]
            if ids_to_delete:
                invoke("deleteNotes", notes=ids_to_delete)
                print(f"[删除] 从 Anki 移除了 {len(ids_to_delete)} 张废弃卡片。")
                
            cards_need_id_writeback = False

            if anki_ids:
                notes_info = invoke("notesInfo", notes=anki_ids)
                anki_cards_data = {}
                for info in notes_info:
                    if isinstance(info, dict) and 'noteId' in info and 'fields' in info:
                        anki_cards_data[info['noteId']] = info['fields']
            else:
                anki_cards_data = {}

            for card in obsidian_cards:
                # 使用新的 HTML 转换函数渲染问题和答案
                obsidian_q_anki_format = convert_qa_to_html(card['question'])
                obsidian_a_anki_format = convert_qa_to_html(card['answer'])

                if card['id']:
                    nid = int(card['id'])
                    if nid in anki_cards_data:
                        current_fields = anki_cards_data[nid]
                        current_q_anki = current_fields.get("问题", {}).get("value", "")
                        current_a_anki = current_fields.get("答案", {}).get("value", "")
                        current_ctx = current_fields.get(UPDATE_FIELD, {}).get("value", "")
                        
                        # 直接对 HTML 代码进行比对，不再进行反向 Markdown 解析
                        updates = {}
                        if current_q_anki != obsidian_q_anki_format: updates["问题"] = obsidian_q_anki_format
                        if current_a_anki != obsidian_a_anki_format: updates["答案"] = obsidian_a_anki_format
                        if current_ctx != new_html_context: updates[UPDATE_FIELD] = new_html_context
                        
                        if updates:
                            invoke("updateNoteFields", note={"id": nid, "fields": updates})
                            print(f"[更新] 更新了卡片 ID {nid} 的字段: {list(updates.keys())}")
                else:
                    new_note = {
                        "deckName": DECK_NAME,
                        "modelName": NOTE_TYPE,
                        "fields": {
                            "问题": obsidian_q_anki_format,
                            "答案": obsidian_a_anki_format,
                            "Advanced URI": source_uri,
                            UPDATE_FIELD: new_html_context
                        },
                        "options": {"allowDuplicate": False},
                        "tags": ["ObsidianAPI", "ManualSync"]
                    }
                    try:
                        new_id_res = invoke("addNotes", notes=[new_note])
                        if new_id_res and new_id_res[0]:
                            card['id'] = str(new_id_res[0])
                            cards_need_id_writeback = True
                            print(f"[新增] 成功添加手动卡片，获得 ID {card['id']}")
                    except Exception as e:
                        print(f"[错误] 添加新卡片失败: {e}")

            if cards_need_id_writeback:
                rewrite_markdown_table(file_path, content, obsidian_cards)
                print(f"[保存] 已将新的 Anki ID 写回笔记 {file}。")

if __name__ == "__main__":
    sync_notes()
