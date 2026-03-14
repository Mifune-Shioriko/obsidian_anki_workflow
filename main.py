import sys
import json
import requests
import os
import re
import markdown
from urllib.parse import quote
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List
from pathlib import Path
from dotenv import load_dotenv # 环境变量

load_dotenv()

# 1. 获取脚本目录
script_dir = os.path.dirname(os.path.abspath(__file__))

# --- 配置区域 ---
ANKI_URL = os.getenv("ANKI_URL", "http://127.0.0.1:8765")
MODEL_NAME = "gemini-3-flash-preview" 
# 在 Python 脚本里改为：
DECK_NAME = os.getenv("ANKI_DECK_NAME", "Obsidian")
NOTE_TYPE = os.getenv("ANKI_NOTE_TYPE", "Obsidian")
VAULT_NAME = os.getenv("OBSIDIAN_VAULT_NAME", "my_obsidian_note")
VAULT_DIR = os.getenv("VAULT_DIR")

# --- 环境变量 ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    print("Error: 未找到 GOOGLE_API_KEY，请检查 .env 文件是否配置正确。")
    sys.exit(1)

# --- 定义数据结构 ---
class Card(BaseModel):
    question: str = Field(description="The question for the flashcard")
    answer: str = Field(description="The concise answer for the flashcard")

class CardResponse(BaseModel):
    cards: List[Card]

# --- 基础与转换函数 ---
def load_file(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return f.read()
    except FileNotFoundError:
        print(f"Error: File not found at {path}")
        sys.exit(1)

def extract_id_from_yaml(content):
    pattern = r'^id:\s*["\']?([^"\s\n\']+)["\']?'
    match = re.search(pattern, content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return None

def construct_advanced_uri(vault_name, note_id):
    safe_vault = quote(vault_name)
    safe_id = quote(note_id)
    return f"obsidian://advanced-uri?vault={safe_vault}&uid={safe_id}"

def convert_to_html(text):
    text = re.sub(r'^\s*---\n.*?\n---\n', '', text, flags=re.DOTALL | re.MULTILINE)
    text = re.sub(r'\$\$(.+?)\$\$', r'\\\\[\1\\\\]', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.+?)(?<!\\)\$', r'\\\\(\1\\\\)', text)
    try:
        html_content = markdown.markdown(text, extensions=['extra', 'nl2br', 'codehilite'])
    except Exception as e:
        print(f"Markdown conversion warning: {e}")
        html_content = f"<pre>{text}</pre>"
    return html_content

def obsidian_to_anki_math(text):
    r"""将 Obsidian 的 $...$ 转换为 Anki 的 \(...\)"""
    if not text: return text
    text = re.sub(r'\$\$(.*?)\$\$', r'\\[\1\\]', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$(.*?)(?<!\\)\$', r'\\(\1\\)', text, flags=re.DOTALL)
    return text

def anki_to_obsidian_math(text):
    r"""将 Anki 的 \(...\) 转换为 Obsidian 的 $...$ 用于精确比对"""
    if not text: return text
    text = re.sub(r'\\\[(.*?)\\\]', r'$$\1$$', text, flags=re.DOTALL)
    text = re.sub(r'\\\((.*?)\\\)', r'$\1$', text, flags=re.DOTALL)
    return text

def process_images_for_anki(text):
    """提取文本中的图片，上传到 Anki 媒体库，并将语法替换为 <img> 标签"""
    if not text:
        return text

    pattern = r'!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'
    matches = set(re.findall(pattern, text))

    if not matches:
        return text

    vault_path = Path(VAULT_DIR)

    for filename in matches:
        filename = filename.strip()
        found_files = list(vault_path.rglob(filename))
        found_files = [p for p in found_files if ".obsidian" not in str(p) and ".trash" not in str(p)]

        if found_files:
            file_path = found_files[0].resolve()

            payload = {
                "action": "storeMediaFile",
                "version": 6,
                "params": {
                    "filename": filename,
                    "path": str(file_path)
                }
            }
            try:
                res = requests.post(ANKI_URL, json=payload).json()
                if res.get('error'):
                    print(f"Warning: Anki 返回图片上传错误 ({filename}): {res['error']}")
            except Exception as e:
                print(f"Warning: 无法连接到 Anki 上传图片: {e}")
            # 将文本中的 ![[filename]] 或 ![[filename|...]] 替换为 HTML <img> 标签
            replace_pattern = r'!\[\[' + re.escape(filename) + r'(?:\|[^\]]+)?\]\]'
            text = re.sub(replace_pattern, f'<img src="{filename}">', text)

    return text

# --- 核心逻辑 ---
def get_ai_response(note_content, system_prompt):
    # 检查是否已填入 API Key
    if not GOOGLE_API_KEY or "YourAPIKeyHere" in GOOGLE_API_KEY:
        print("Error: 请在脚本的配置区域填入真实的 GOOGLE_API_KEY。")
        sys.exit(1)

    # 直接使用全局配置的 API Key 实例化 Client
    client = genai.Client(api_key=GOOGLE_API_KEY)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=note_content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=CardResponse,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(thinking_level='medium')
            )
        )
        return response.parsed
    except Exception as e:
        print(f"Google Gen AI Error: {e}")
        sys.exit(1)

def add_to_anki(parsed_response, source_uri, original_text):
    if not parsed_response or not parsed_response.cards:
        print("Warning: AI returned no cards.")
        return []

    # 核心改动：仅在这里对原来的笔记进行图片提取、上传和标签替换
    processed_original_text = process_images_for_anki(original_text)
    rendered_context = convert_to_html(processed_original_text)
    
    notes = []
    
    for card in parsed_response.cards:
        note = {
            "deckName": DECK_NAME,
            "modelName": NOTE_TYPE,
            "fields": {
                "问题": convert_to_html(card.question), # 保持原样，不处理图片
                "答案": convert_to_html(card.answer),   # 保持原样，不处理图片
                "Advanced URI": source_uri,
                "原来的笔记": rendered_context          # 使用处理并渲染过图片的文本
            },
            "options": {"allowDuplicate": False},
            "tags": ["ObsidianAPI", "Gemini"]
        }
        notes.append(note)

    payload = {
        "action": "addNotes",
        "version": 6,
        "params": {"notes": notes}
    }
    
    added_cards = []
    try:
        response = requests.post(ANKI_URL, json=payload)
        result = response.json()
        if result.get('error'):
            print(f"Anki Connect Error: {result['error']}")
        else:
            note_ids = result.get('result', [])
            valid_ids_count = len([i for i in note_ids if i])
            print(f"Success! Added {valid_ids_count} cards to Anki.")
            
            for i, note_id in enumerate(note_ids):
                if note_id:
                    added_cards.append({
                        "question": parsed_response.cards[i].question,
                        "answer": parsed_response.cards[i].answer,
                        "id": str(note_id)
                    })
    except Exception as e:
        print(f"Anki Connection Failed: {e}")
        
    return added_cards

def append_cards_to_markdown(filepath, new_cards):
    if not new_cards:
        return
        
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        content = f.read()
        
    table_header = "\n| 问题 | 答案 | Anki ID |\n| ---- | ---- | ------- |\n"
    new_rows = ""
    
    for card in new_cards:
        # 先进行数学公式符号的转换，再处理 Markdown 表格需要的转义
        q_math_fixed = anki_to_obsidian_math(card['question'])
        a_math_fixed = anki_to_obsidian_math(card['answer'])
        
        q = q_math_fixed.replace('\n', '<br>').replace('|', '\\|')
        a = a_math_fixed.replace('\n', '<br>').replace('|', '\\|')
        new_rows += f"| {q} | {a} | {card['id']} |\n"

    if "## 卡片" in content:
        if "| 问题 | 答案" in content:
            updated_content = content.rstrip() + "\n" + new_rows
        else:
            updated_content = content.rstrip() + "\n" + table_header + new_rows
    else:
        updated_content = content.rstrip() + "\n\n## 卡片\n" + table_header + new_rows
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(updated_content)
        
    print(f"Successfully appended {len(new_cards)} rows to the Obsidian note.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py <note_path>")
        sys.exit(1)

    note_path = sys.argv[1]
    note_content = load_file(note_path)

    note_id = extract_id_from_yaml(note_content)
    if not note_id:
        print("Error: No 'id' field found in YAML frontmatter.")
        sys.exit(1)
    
    advanced_uri = construct_advanced_uri(VAULT_NAME, note_id)
    print(f"Target Note ID: {note_id}")

    prompt_path = os.path.join(script_dir, 'prompt.txt')
    system_prompt = load_file(prompt_path)
    
    print(f"Generating cards using {MODEL_NAME}...")
    parsed_data = get_ai_response(note_content, system_prompt)
    
    added_cards = add_to_anki(parsed_data, advanced_uri, note_content)
    
    if added_cards:
        append_cards_to_markdown(note_path, added_cards)
