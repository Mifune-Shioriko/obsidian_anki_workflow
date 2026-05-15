from google import genai
from google.genai import types
import utils
import sys
from pathlib import Path

# ==========================================
# 核心修复区：动态向上寻找 agent_tools.py
# ==========================================
current_dir = Path(__file__).resolve().parent
# 一层层往上找，直到找到包含 agent_tools.py 的目录
while current_dir.name and current_dir.name != "/":
    if (current_dir / "agent_tools.py").exists():
        if str(current_dir) not in sys.path:
            sys.path.insert(0, str(current_dir))
        break
    current_dir = current_dir.parent

try:
    from agent_tools import agent_tools
except Exception as e:
    print(f"⚠️ Warning: 加载 agent_tools 失败，Leo 的工具将不可用。错误明细: {e}")
    agent_tools = []

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

def handle(command, history, note_path, full_content):
    try:
        # 分离历史记录与当前提问
        chat_history = history[:-1]
        
        # 安全地提取 last_message，无论它是字典还是对象
        last_message = ""
        last_turn_parts = history[-1].get("parts", [])
        for part in last_turn_parts:
            if isinstance(part, dict) and "text" in part:
                last_message += part["text"] + "\n"
            elif hasattr(part, 'text') and getattr(part, 'text', None):
                last_message += getattr(part, 'text') + "\n"
        last_message = last_message.strip()

        # 将字典格式的 history 强转为 SDK 要求的 Content 对象
        formatted_history = []
        for turn in chat_history:
            # 安全提取文本内容，防范因为其他 agent 修改了数据结构导致这里崩溃
            text_content = ""
            for part in turn.get("parts", []):
                if isinstance(part, dict) and "text" in part:
                    text_content += part["text"] + "\n"
                elif hasattr(part, 'text') and getattr(part, 'text', None):
                    text_content += getattr(part, 'text') + "\n"
                    
            text_content = text_content.strip()
            # 过滤掉系统内部的 Debug 输出或特殊标记，防止干扰历史解析
            if text_content.startswith("[Debug]"):
                text_content = "[系统过滤的调试信息]"
                
            formatted_part = types.Part.from_text(text=text_content)
            formatted_content = types.Content(role=turn["role"], parts=[formatted_part])
            formatted_history.append(formatted_content)

        # 提取打标签相关的工具
        tag_tools = [tool for tool in agent_tools if 'tag' in tool.__name__]

        # 初始化 Chat 会话
        chat = client.chats.create(
            model=utils.MODEL_NAME,
            history=formatted_history, 
            config=types.GenerateContentConfig(
                tools=tag_tools if tag_tools else None, 
                temperature=0.6,
                system_instruction=(
                    "你是一个 Anki 卡片打标签专员。\n"
                    "1. 【核心工作流】：你的唯一任务是接收用户提供的卡片 ID 和标签名，然后调用 `add_tag_to_cards` 工具为它们打上标签。\n"
                    "2. 【约束条件】：\n"
                    "   - 如果用户要求打标签但没有提供具体的卡片 ID（可能是希望你通过上下文获取），请在聊天记录中寻找最近一次其他 Agent（比如知识导师）提到的卡片 ID（通常标记为 [ID: xxxx]）。\n"
                    "   - 如果上下文中完全找不到任何 ID，你需要礼貌地向用户索要具体的卡片 ID 列表。\n"
                    "   - 永远不要尝试自己去搜索卡片，你没有搜索的权限。\n"
                    "3. 成功执行操作后，用简短的语言通知用户。"
                )
            )
        )
        
        # 发送最新请求，触发工具调用机制
        print(f"[Debug] Sending message to LLM: {last_message}")
        res = chat.send_message(last_message)
        print(f"[Debug] LLM response received. Length: {len(res.text) if res.text else 0}")
        return res.text
        
    except Exception as e:
        print(f"[Error in tag.py]: {e}")
        return f"❌ **Leo Agent 发生错误**: {e}"
