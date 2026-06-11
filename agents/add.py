from pydantic import BaseModel, Field
import model_client as genai
from model_client import types
import utils
import re

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

class Card(BaseModel):
    question: str = Field(description="卡片的问题部分")
    answer: str = Field(description="卡片的答案部分")
    id: str = Field(description="卡片的 Anki ID。新卡片必须为空字符串。原卡片保留原 ID。")

class DoctorResponse(BaseModel):
    cards: list[Card] = Field(description="根据讨论和笔记内容决定生成的全新原子化 Anki 卡片列表")

def find_bold_texts(text):
    # Matches markdown bold using **...** or __...__
    matches = re.findall(r'\*\*([\s\S]+?)\*\*|__([\s\S]+?)__', text)
    results = []
    for match in matches:
        val = match[0] or match[1]
        val = val.strip()
        if val and val not in results:
            results.append(val)
    return results

def handle(command, history, note_path, full_content):
    current_cards_dict = utils.parse_markdown_table(full_content)
    has_cards_section = bool(re.search(r'^## 卡片\s*$', full_content, re.MULTILINE))
    
    full_header, context_header, chat_content, related_body, _ = utils.parse_document(full_content)
    
    # Extract bold texts from both the note's context header and the dialogue history
    bold_contents = []
    if context_header:
        bold_contents.extend(find_bold_texts(context_header))
    for turn in history:
        for part in turn.get("parts", []):
            text = part.get("text", "")
            bold_contents.extend(find_bold_texts(text))
    
    # Deduplicate while preserving order
    unique_bolds = []
    for item in bold_contents:
        if item not in unique_bolds:
            unique_bolds.append(item)
            
    if not unique_bolds:
        return (
            "⚠️ **Add Agent 提示**：未在当前笔记正文或对话历史中检测到加粗的文本（如 `**加粗内容**`）。\n"
            "本 Agent 采用定向制卡模式，请先使用粗体圈定您想要制卡的核心知识点，然后再试。"
        )

    bold_list_str = "\n".join([f"- {item}" for item in unique_bolds])
    
    system_instruction = (
        "你是一个极其严谨且具备深厚教学诊断能力的 Anki 卡片制作专家。你的任务是根据用户指定的【加粗知识内容】，提炼出高度原子化且最具核心考点价值的新卡片。\n\n"
        "【双重筛选制卡决策模型（单次完成）】\n"
        "你不能自主无差别建卡，也无需自行筛选高价值考点。你必须**严格且仅针对**下面给出的【加粗知识内容】进行卡片提炼：\n"
        "1. 脑海中原子化拆分：首先针对每一个被划定的加粗文本进行虚拟拆分，列出所有可能的最微小、单一的考点。\n"
        "2. 50% 核心筛选过滤：在输出前进行严格筛选，仅保留其中最核心、最具考点价值的前 50% 的卡片（若只有 1 个考点则保留 1 个，多个则向上取整，如 3 个保留 2 个，4 个保留 2 个）。主动过滤掉冗余、次要、过于琐碎或容易由常识推导出的考点，确保卡片少而精。\n"
        "3. 严禁超纲：严禁针对加粗范围之外的任何笔记内容或背景信息制作卡片。\n\n"
        "【卡片生成黄金规则（必遵守）】\n"
        "- 极限原子化原则：被保留输出的每一张卡片只能测试一个不可再分的、最微小的单一知识单元。问题直接无歧义，答案极度简洁，通常只包含一个词或短语。\n"
        "- 去语境化 (Decontextualized)：卡片必须完全自包含。严禁在问题或答案中出现‘根据上述内容’、‘根据上面的对话’、‘正如刚才所说’等任何对当前上下文的指代词。\n"
        "- LaTeX 强制定界符：由于卡片直接呈现在 Obsidian 中，凡是属于科学、数学、化学等领域的变量、符号、公式、上下标，必须使用标准美元符号包裹！行内公式必须使用 $ ... $ 包裹（例如：$PP_i$，$Mg^{2+}$），块级公式必须使用 $$ ... $$ 包裹。绝对禁止使用 \\( ... \\) 或 \\[ ... \\]。\n\n"
        "【输出规范】\n"
        "请严格遵循 JSON Schema 返回 `cards` 列表。所有新卡片的 `id` 必须留空字符串 `\"\"`。\n\n"
        f"【加粗知识内容（必须以此为唯一制卡依据）】\n{bold_list_str}"
    )
    
    if context_header.strip():
        clean_header = context_header.strip()
        if clean_header.endswith("---"):
            clean_header = clean_header[:-3].strip()
        if clean_header:
            system_instruction += f"\n\n请参考以下当前笔记的正文内容来获取加粗部分的背景上下文，以便更准确地理解和提炼卡片，但卡片考点本身必须完全属于加粗部分：\n{clean_header}"

    last_user_idx = len(history) - 1
    original_text = history[last_user_idx]["parts"][0]["text"]

    history[last_user_idx]["parts"][0]["text"] = (
        f"{original_text}\n\n[系统注入] 请直接根据上述指定的【加粗知识内容】及相关笔记正文，根据【双重筛选制卡决策模型（单次完成）】生成最核心的高价值全新原子化卡片。所有新卡片的 id 必须为空字符串 \"\"。\n"
        f"注意：必须在脑海中充分拆分，并在输出前严格过滤筛选，仅返回最具核心考点价值的 50% 核心卡片（向上取整，卡片少而精）。"
    )

    try:
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=history,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=DoctorResponse,
                temperature=0.2
            )
        )
        parsed: DoctorResponse = res.parsed
        
        new_cards = []
        for card in parsed.cards:
            new_cards.append({
                "question": utils.unbold_text(card.question),
                "answer": utils.unbold_text(card.answer),
                "id": ""
            })
            
        if not new_cards:
            return "⚠️ **Add Agent 提示**：没有提炼出合适的新卡片。"

        # Strip the last user command block (the lines starting with > at the end of chat_content)
        lines = chat_content.rstrip().split('\n')
        last_quote_start = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith('>'):
                last_quote_start = i
            elif last_quote_start != -1:
                break
                
        if last_quote_start != -1:
            cleaned_chat_lines = lines[:last_quote_start]
            while cleaned_chat_lines and not cleaned_chat_lines[-1].strip():
                cleaned_chat_lines.pop()
            chat_content = '\n'.join(cleaned_chat_lines)

        # Unbold both full_header and chat_content to clean up bold marks from the note
        cleaned_full_header = utils.unbold_text(full_header)
        cleaned_chat_content = utils.unbold_text(chat_content)

        # Combine all the new cards with existing ones
        # If there are existing cards in current_cards_dict, they are preserved intact (including their Anki IDs and content)
        # New cards are appended to the bottom of the list.
        current_cards_dict.extend(new_cards)
        
        # Build the updated content without the @add dialogue block
        base_body = f"{cleaned_full_header}{cleaned_chat_content}".rstrip()
        new_content = f"{base_body}\n\n"
        if related_body:
            new_content += f"{related_body.strip()}\n\n"
        
        # Construct the cards table block
        # If there are no existing cards and no '## 卡片' section, this creates a new section.
        # If there is already a '## 卡片' section, it overwrites it with the combined table, appending new cards to the bottom.
        new_table_block = "## 卡片\n\n" + utils.format_cards_to_table(current_cards_dict) + "\n"
        new_content += new_table_block
        
        # Write back to the file
        with open(note_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        if has_cards_section:
            print(f"✅ [Add Agent] Successfully appended {len(new_cards)} new cards to the existing cards table and cleaned bold markers.")
        else:
            print(f"✅ [Add Agent] Successfully created a new cards table with {len(new_cards)} cards and cleaned bold markers.")
            
        return None  # Return None to prevent router.py from inserting any AI response

    except Exception as e:
        return f"❌ **Add Agent 发生错误**: {e}"
