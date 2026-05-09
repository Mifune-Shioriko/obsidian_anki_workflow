import sys
import re
import utils
import os
import importlib
from pathlib import Path

# ================= 动态注册表 (Registry) =================
AGENT_REGISTRY = {}
agents_dir = Path(__file__).resolve().parent / "agents"

# 动态加载 agents 目录下的所有模块
for file_path in agents_dir.glob("*.py"):
    if file_path.name == "__init__.py":
        continue
    agent_name = file_path.stem
    try:
        module = importlib.import_module(f"agents.{agent_name}")
        if hasattr(module, "handle"):
            AGENT_REGISTRY[agent_name] = module.handle
    except Exception as e:
        print(f"Failed to load agent {agent_name}: {e}")

# 兼容旧版的调用
if "search" in AGENT_REGISTRY:
    AGENT_REGISTRY["leo"] = AGENT_REGISTRY["search"]
    AGENT_REGISTRY["anki"] = AGENT_REGISTRY["search"]
# =====================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python router.py <note_path>")
        sys.exit(1)

    note_path = sys.argv[1]
    with open(note_path, 'r', encoding='utf-8-sig') as f:
        full_content = f.read()

    # 1. 解析基础文档结构
    full_header, context_header, chat_content, cards_body = utils.parse_document(full_content)
    if not chat_content.strip(): 
        print("Error: chat_content is empty.")
        sys.exit(1)

    # 2. 解析为基础 history
    history = utils.parse_markdown_to_history(chat_content)
    if not history:
        print("Error: history is empty after parsing.")
        sys.exit(1)
    
    # 修复：如果历史第一条是 model（通常是因为用户在开头直接粘贴了未引用的文本），
    # 把它转换成 user，而不是粗暴地删掉。
    if history and history[0]["role"] == "model":
        history[0]["role"] = "user"
        print("Info: Changed initial unquoted text from model to user.")
        
    if not history:
        print("Error: history is empty after processing.")
        sys.exit(1)

    if history[-1]["role"] == "model":
        print("Error: The last message is from the model, skipping.")
        sys.exit(1)

    # 3. 路由识别与指令清理
    last_user_text = history[-1]["parts"][0]["text"].strip()
    route_match = re.match(r'^@(\w+)(?:\s+(.*))?$', last_user_text, re.DOTALL)
    
    agent_name = "default"
    command_text = ""
    
    if route_match:
        agent_name = route_match.group(1)
        command_text = route_match.group(2) or ""
        if not command_text.strip():
            command_text = "请继续执行"
        # 清理用户提问中的 @agent 指令，避免干扰 AI
        history[-1]["parts"][0]["text"] = command_text
    else:
        command_text = last_user_text
        if not command_text.strip():
            command_text = "请继续执行"

    # 4. 合并相邻的同角色对话 (合并 consecutive roles)
    merged_history = []
    for turn in history:
        if merged_history and merged_history[-1]["role"] == turn["role"]:
            merged_history[-1]["parts"][0]["text"] += "\n\n" + turn["parts"][0]["text"]
        else:
            merged_history.append(turn)
    history = merged_history

    # 5. 执行路由分发，并获取 Agent 的回复文本
    if agent_name in AGENT_REGISTRY:
        print(f"正在将请求路由给 Agent: {agent_name}")
        
        # 注意这里的传参，需与 default.py 的 def handle(command, history, note_path, full_content) 对应
        reply_text = AGENT_REGISTRY[agent_name](
            command=command_text,
            history=history,
            note_path=note_path,
            full_content=full_content
        )
        
        # 5. 接力写入文件：拿到 Agent 返回的文本后，写回 Markdown
        if reply_text:
            # 重新读取文件，因为 Agent（如 addnew, revise）可能已经更新了底部的卡片表格
            with open(note_path, 'r', encoding='utf-8-sig') as f:
                new_full_content = f.read()
            _, _, _, new_cards_body = utils.parse_document(new_full_content)
            
            # 格式化清洗（比如处理空格、列表缩进等）
            clean_reply = utils.sanitize_format(reply_text)
            utils.insert_ai_response(note_path, full_header, chat_content, clean_reply, new_cards_body)
            print("Success! AI 回复已成功插入。")
        else:
            print("Warning: Agent 返回了空内容，未修改文件。")
            
    else:
        print(f"Error: 未找到对应的 Agent '{agent_name}'")
        sys.exit(1)

if __name__ == "__main__":
    main()
