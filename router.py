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
if "find" in AGENT_REGISTRY:
    AGENT_REGISTRY["leo"] = AGENT_REGISTRY["find"]
    AGENT_REGISTRY["anki"] = AGENT_REGISTRY["find"]
# =====================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python router.py <note_path>")
        sys.exit(1)

    note_path = sys.argv[1]
    with open(note_path, 'r', encoding='utf-8-sig') as f:
        full_content = f.read()

    # 1. 解析基础文档结构
    full_header, context_header, chat_content, related_body, cards_body = utils.parse_document(full_content)
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
    
    # 提取所有 @ 单词并匹配合法的已注册 Agent
    all_agent_matches = re.findall(r'@(\w+)', last_user_text)
    valid_agents = [name for name in all_agent_matches if name in AGENT_REGISTRY]
    
    # 如果检测到非法的 Agent 调用，给予友好报错
    invalid_agents = [name for name in all_agent_matches if name not in AGENT_REGISTRY]
    if invalid_agents:
        print(f"Error: 管道中检测到未注册或无效的 Agent: {invalid_agents}")
        sys.exit(1)

    if len(valid_agents) > 1:
        print(f"✨ 激活 Agent 链式管道: {' ➔ '.join(valid_agents)}")
        
        # 提取第一个 Agent 的干净指令，剔除仅包含 @ 符号及管道连字符的行或部分
        cleaned_lines = []
        for line in last_user_text.split('\n'):
            stripped_line = line.strip()
            # 去除整行中的所有 @agent 词汇
            line_no_agents = re.sub(r'@\w+', '', stripped_line).strip()
            # 如果去除 @agent 后，整行只剩下管道符号（如 |, ->, %, >）或为空，则忽略该行
            if not re.sub(r'[\s|>\-%]+', '', line_no_agents).strip():
                continue
            # 否则保留该行，并移除非内容的 @agent 词汇
            cleaned_lines.append(re.sub(r'@\w+', '', line).rstrip())
            
        cleaned_command = '\n'.join(cleaned_lines).strip()
        # 裁剪掉首尾多余的管道连字符及空格
        cleaned_command = re.sub(r'^[\s|>\-%]+|[\s|>\-%]+$', '', cleaned_command).strip()
        
        if not cleaned_command:
            cleaned_command = "请继续执行"
            
        current_content = full_content
        
        for idx, agent_name in enumerate(valid_agents):
            full_header, context_header, chat_content, related_body, cards_body = utils.parse_document(current_content)
            
            # 从第二步开始，追加 simulated user 提问节点
            if idx > 0:
                chat_content = chat_content.rstrip() + f"\n\n> @{agent_name}"
                utils.insert_ai_response(note_path, full_header, chat_content, "", related_body, cards_body)
                
                # 重新加载写入后的文档状态
                with open(note_path, 'r', encoding='utf-8-sig') as f:
                    current_content = f.read()
                full_header, context_header, chat_content, related_body, cards_body = utils.parse_document(current_content)
            
            # 解析本步的对话历史，确保相邻同角色已被合并
            step_history = utils.parse_markdown_to_history(chat_content)
            if step_history and step_history[0]["role"] == "model":
                step_history[0]["role"] = "user"
                
            merged_history = []
            for turn in step_history:
                if merged_history and merged_history[-1]["role"] == turn["role"]:
                    merged_history[-1]["parts"][0]["text"] += "\n\n" + turn["parts"][0]["text"]
                else:
                    merged_history.append(turn)
            step_history = merged_history
            
            # 确定本步命令
            step_command = cleaned_command if idx == 0 else "请继续执行"
            step_history[-1]["parts"][0]["text"] = step_command
            
            print(f"[{idx+1}/{len(valid_agents)}] 正在接力给 Agent: {agent_name}...")
            
            # 调用 Agent 句柄
            reply_text = AGENT_REGISTRY[agent_name](
                command=step_command,
                history=step_history,
                note_path=note_path,
                full_content=current_content
            )
            
            if reply_text:
                # 重新加载物理文件（防止部分 Agent 内部对卡片或正文已经做了持久化更新而导致冲突）
                with open(note_path, 'r', encoding='utf-8-sig') as f:
                    temp_content = f.read()
                temp_full_header, _, _, temp_related_body, temp_cards_body = utils.parse_document(temp_content)
                
                clean_reply = utils.sanitize_format(reply_text)
                utils.insert_ai_response(note_path, temp_full_header, chat_content, clean_reply, temp_related_body, temp_cards_body)
                print(f"[{idx+1}/{len(valid_agents)}] Agent {agent_name} 的回复成功插入。")
                
                # 错误/警告检测与管道熔断 (Error detection and early-exit)
                if reply_text.strip().startswith(("❌", "⚠️")) or "发生错误" in reply_text:
                    print(f"⚠️ 检测到 Agent {agent_name} 执行出错或有重要提示。管道已终止。")
                    sys.exit(1)
            else:
                print(f"[{idx+1}/{len(valid_agents)}] Agent {agent_name} 执行完毕（无文本返回，可能已就地修改表格）。")
                
            # 刷新最新内容供下一步使用
            with open(note_path, 'r', encoding='utf-8-sig') as f:
                current_content = f.read()
                
        print("🎉 链式管道调用全部执行完毕！")
        return

    # 优先匹配开头为 @agent 的指令 (如: @explain xxx)
    route_match_start = re.match(r'^@(\w+)(?:\s+(.*))?$', last_user_text, re.DOTALL)
    # 其次匹配结尾为 @agent 的指令 (如: xxx\n@explain)
    route_match_end = re.search(r'^(.*?)\s*@(\w+)\s*$', last_user_text, re.DOTALL)
    
    agent_name = "default"
    command_text = ""
    
    if route_match_start:
        agent_name = route_match_start.group(1)
        command_text = route_match_start.group(2) or ""
        if not command_text.strip():
            command_text = "请继续执行"
        # 清理用户提问中的 @agent 指令，避免干扰 AI
        history[-1]["parts"][0]["text"] = command_text
    elif route_match_end:
        agent_name = route_match_end.group(2)
        command_text = route_match_end.group(1) or ""
        if not command_text.strip():
            command_text = "请继续执行"
        # 清理用户提问中的 @agent 指令，避免干扰 AI
        history[-1]["parts"][0]["text"] = command_text
    else:
        command_text = last_user_text
        if not command_text.strip():
            command_text = "请继续执行"
        
        # 只要有一轮使用了某 agent，之后都默认是调用该 agent，直到调用另一个 agent
        detected_agent = None
        for turn in reversed(history[:-1]):
            if turn["role"] == "user":
                turn_text = turn["parts"][0]["text"].strip()
                match_start = re.match(r'^@(\w+)(?:\s+(.*))?$', turn_text, re.DOTALL)
                match_end = re.search(r'^(.*?)\s*@(\w+)\s*$', turn_text, re.DOTALL)
                if match_start:
                    detected_agent = match_start.group(1)
                    break
                elif match_end:
                    detected_agent = match_end.group(2)
                    break
        
        if detected_agent and detected_agent in AGENT_REGISTRY:
            agent_name = detected_agent

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
            _, _, _, _, new_cards_body = utils.parse_document(new_full_content)
            
            # 格式化清洗（比如处理空格、列表缩进等）
            clean_reply = utils.sanitize_format(reply_text)
            utils.insert_ai_response(note_path, full_header, chat_content, clean_reply, related_body, new_cards_body)
            print("Success! AI 回复已成功插入。")
        else:
            print("Warning: Agent 返回了空内容，未修改文件。")
            
    else:
        print(f"Error: 未找到对应的 Agent '{agent_name}'")
        sys.exit(1)

if __name__ == "__main__":
    main()
