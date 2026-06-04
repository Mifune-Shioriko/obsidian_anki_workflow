import sys
import json
import requests
import os
import re
import markdown
import html
from dotenv import load_dotenv
from urllib.parse import quote, unquote

load_dotenv()

# ================= 配置区域 =================
VAULT_ROOT = os.getenv("VAULT_DIR", "")
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
synced_media_in_run = set()

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


# ================= 图片处理函数 =================
def process_media_links(text, add_spacing=False):
    """
    将 Obsidian 的 [[image.png]] 转换为 Anki 的 <img> 标签。
    add_spacing: 是否在图片前后强制增加换行（用于表格里的问题和答案字段）
    """
    if not text: return text

    # 核心修改：允许拓展名后面存在 | 及其跟随 the 缩放参数
    pattern = r'(!?)\[\[([^\]|]+\.(?:png|jpe?g|gif|svg|webp|bmp))(?:\|[^\]]+)?\]\]'
    
    def replace_match(match):
        prefix = match.group(1)
        filename = match.group(2).strip()
        file_path = os.path.join(FILES_DIR, filename)
        
        if os.path.exists(file_path):
            if filename not in synced_media_in_run:
                try:
                    invoke("storeMediaFile", filename=filename, path=file_path)
                    print(f"[媒体] 成功同步图片: {filename}")
                    synced_media_in_run.add(filename)
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

def render_markdown_safely(text, add_spacing=False):
    """
    通用渲染函数：使用纯字母数字占位符保护公式，防止 markdown 引擎吃掉下划线
    """
    if not text: return ""
    
    math_placeholders = {}
    
    # 核心修复：占位符绝对不能包含下划线(_)或星号(*)等 Markdown 保留字
    def block_math_repl(match):
        key = f"MATHBLOCKPLACEHOLDER{len(math_placeholders)}K"
        # 直接使用标准的 Anki 块级公式语法 \[ ... \]
        math_placeholders[key] = f"\\[{match.group(1)}\\]"
        return key

    def inline_math_repl(match):
        key = f"MATHINLINEPLACEHOLDER{len(math_placeholders)}K"
        # 直接使用标准的 Anki 内联公式语法 \( ... \)
        math_placeholders[key] = f"\\({match.group(1)}\\)"
        return key

    # 1. 提取公式并替换为占位符 (优先提取块级，再提取内联)
    text = re.sub(r'\$\$(.*?)\$\$', block_math_repl, text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.*?)(?<!\\)\$', inline_math_repl, text, flags=re.DOTALL)

    # 2. 处理图片
    text = process_media_links(text, add_spacing=add_spacing)

    # 3. 转换为 HTML (此时公式变成了一串纯英文字母，绝对安全)
    try:
        html_content = markdown.markdown(text, extensions=['extra', 'nl2br', 'codehilite'])
    except Exception as e:
        print(f"[警告] Markdown 渲染失败: {e}")
        html_content = f"<pre>{text}</pre>"

    # 4. 把公式原封不动地填回 HTML 中
    for key, val in math_placeholders.items():
        html_content = html_content.replace(key, val)

    return html_content

def convert_to_html(text):
    if not text: return ""
    
    # 优先移除 ## 相关笔记 及其后续的所有段落（包括 ## 卡片）
    related_match = re.search(r'^##\s*(?:相关笔记|Related\s+Notes)\s*$', text, re.MULTILINE | re.IGNORECASE)
    if related_match:
        text = text[:related_match.start()].strip()
    elif "## 卡片" in text:
        text = text.split("## 卡片")[0].strip()
        
    # 移除 YAML Frontmatter
    text = re.sub(r'^\s*---\n.*?\n---\n', '', text, flags=re.DOTALL | re.MULTILINE)
    
    return render_markdown_safely(text, add_spacing=False)


def convert_qa_to_html(text):
    return render_markdown_safely(text, add_spacing=True)

def parse_markdown_table(content):
    if "## 卡片" not in content:
        return []

    table_text = content.split("## 卡片")[1]
    cards = []

    for line in table_text.strip().split('\n'):
        if not line.strip().startswith('|'): continue
        if '----' in line: continue
        if 'Anki ID' in line: continue

        # 核心修复：使用负向零宽断言，只在没有转义符 \ 的 | 处进行分割
        parts = [p.strip() for p in re.split(r'(?<!\\)\|', line)[1:-1]]

        if len(parts) >= 2:
            q = parts[0].replace('\\|', '|').replace('<br>', '\n')
            a = parts[1].replace('\\|', '|').replace('<br>', '\n')
            nid = parts[2] if len(parts) > 2 and parts[2].strip() else None

            # 过滤掉完全空的行
            if not q.strip() and not a.strip():
                continue

            # 安全校验：确保 nid 是纯数字，防止未来出现意外的非法字符
            if nid and not nid.isdigit():
                print(f"[警告] 检测到非法的 Anki ID: {nid}，已忽略。请检查该行格式：\n{line}")
                nid = None

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

    print("正在从 Anki 预载入所有已同步的卡片信息...")
    anki_notes_by_uid = {}
    try:
        # 1. 查找所有通过本系统生成的 Anki 卡片
        all_script_notes = invoke("findNotes", query=f'"{SEARCH_FIELD}:obsidian://advanced-uri*"' )
        if all_script_notes:
            # 2. 批量获取这些卡片的详细字段与信息
            all_notes_info = invoke("notesInfo", notes=all_script_notes)
            for info in all_notes_info:
                fields = info.get("fields", {})
                uri_field = fields.get(SEARCH_FIELD, {}).get("value", "")
                
                # 过滤并提取属于当前库的卡片，使用 html.unescape 处理 HTML 实体转义
                uri_clean = html.unescape(uri_field)
                match_uid = re.search(r'uid=([^&"]+)', uri_clean)
                match_vault = re.search(r'vault=([^&"]+)', uri_clean)
                
                if match_uid and match_vault:
                    note_uid = unquote(match_uid.group(1))
                    vault_name = unquote(match_vault.group(1))
                    
                    if vault_name == VAULT_NAME:
                        if note_uid not in anki_notes_by_uid:
                            anki_notes_by_uid[note_uid] = []
                        anki_notes_by_uid[note_uid].append(info)
            print(f"预载成功！共加载来自 Anki 的 {len(all_script_notes)} 张卡片（属于当前库 {VAULT_NAME} 的有 {sum(len(v) for v in anki_notes_by_uid.values())} 张）。")
    except Exception as e:
        print(f"警告：从 Anki 预加载卡片数据失败: {e}。将退回到空数据继续同步。")

    print(f"正在扫描文件夹: {TARGET_FOLDER_NAME} ...")
    active_obsidian_notes = set()
    
    for root, dirs, files in os.walk(TARGET_FULL_PATH):
        for file in files:
            if not file.endswith(".md"): continue
            
            file_path = os.path.join(root, file)
            content = load_file(file_path)
            if not content: continue
            
            note_id = extract_id_from_yaml(content)
            if not note_id: continue

            # 核心优化：如果笔记本身根本没有“## 卡片”标记，代表没有制卡需求，直接跳过处理，大幅节省正则与转换开销
            if "## 卡片" not in content:
                continue

            print(f"\n--- 处理笔记: {file} ---")
            source_uri = f"obsidian://advanced-uri?vault={quote(VAULT_NAME)}&uid={quote(note_id)}"
            new_html_context = convert_to_html(content)

            obsidian_cards = parse_markdown_table(content)
            
            if obsidian_cards:
                active_obsidian_notes.add(note_id)
            
            # 核心优化：直接从内存数据库中读取已有的 card id 列表，无需再通过网络 findNotes
            existing_notes_info = anki_notes_by_uid.get(note_id, [])
            anki_ids = [info['noteId'] for info in existing_notes_info]
            
            if not anki_ids and not obsidian_cards:
                continue

            obsidian_known_ids = [int(c['id']) for c in obsidian_cards if c['id']]
            
            ids_to_delete = [i for i in anki_ids if i not in obsidian_known_ids]
            if ids_to_delete:
                invoke("deleteNotes", notes=ids_to_delete)
                print(f"[删除] 从 Anki 移除了 {len(ids_to_delete)} 张废弃卡片。")
                
            table_needs_rewrite = False

            # 直接构建 anki_cards_data，无需网络 notesInfo 请求
            anki_cards_data = {}
            for info in existing_notes_info:
                if isinstance(info, dict) and 'noteId' in info and 'fields' in info:
                    anki_cards_data[info['noteId']] = info

            for card in obsidian_cards:
                # 使用新的 HTML 转换函数渲染问题和答案
                obsidian_q_anki_format = convert_qa_to_html(card['question'])
                obsidian_a_anki_format = convert_qa_to_html(card['answer'])

                nid = int(card['id']) if card['id'] and card['id'].isdigit() else None
                if nid and nid in anki_cards_data:
                    # 加上 .get("fields", {}) 拿到字段
                    current_fields = anki_cards_data[nid].get("fields", {})
                    
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
                            table_needs_rewrite = True
                            print(f"[新增] 成功添加手动卡片，获得 ID {card['id']}")
                    except Exception as e:
                        print(f"[错误] 添加新卡片失败: {e}")

            if table_needs_rewrite:
                rewrite_markdown_table(file_path, content, obsidian_cards)
                print(f"[保存] 已更新 Markdown 表格（ID 或 挂起标签）: {file}")

    # 扫描结束后的全局孤儿卡片清理
    print("\n--- 全局清理孤儿卡片 ---")
    try:
        # 核心优化：由于我们在最开始已经获取了所有的 notesInfo，直接在内存中比对，无需再次调用 API！
        global_ids_to_delete = []
        for note_uid, infos in anki_notes_by_uid.items():
            if note_uid not in active_obsidian_notes:
                for info in infos:
                    global_ids_to_delete.append(info["noteId"])
                        
        if global_ids_to_delete:
            invoke("deleteNotes", notes=global_ids_to_delete)
            print(f"[全局清理] 成功移除了 {len(global_ids_to_delete)} 张孤儿卡片（对应的 Obsidian 笔记已被删除或移除卡片区）。")
        else:
            print("[全局清理] 未发现孤儿卡片，状态健康。")
    except Exception as e:
        print(f"[全局清理] 检查孤儿卡片时发生错误: {e}")

if __name__ == "__main__":
    sync_notes()
