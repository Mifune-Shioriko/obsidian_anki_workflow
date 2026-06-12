import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import utils
import agent_tools


def handle(command, history, note_path, full_content):
    all_chat_text = ""
    for turn in history:
        for part in turn.get("parts", []):
            if isinstance(part, dict) and "text" in part:
                all_chat_text += part["text"] + "\n"
            elif not isinstance(part, dict) and hasattr(part, 'text') and getattr(part, 'text', None):
                all_chat_text += getattr(part, 'text') + "\n"

    chain_results = agent_tools.chain_search_notes(all_chat_text)

    if not chain_results:
        return "未找到相关笔记。"

    output_lines = ["为您找到以下笔记：", ""]
    for entry in chain_results:
        output_lines.append(f"- [[{entry['title']}]]")

    return "\n".join(output_lines)
