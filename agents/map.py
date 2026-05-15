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

        # 提取搜索相关的工具
        map_tools = [tool for tool in agent_tools if 'search' in tool.__name__]

        # 初始化 Chat 会话
        chat = client.chats.create(
            model=utils.MODEL_NAME,
            history=formatted_history, 
            config=types.GenerateContentConfig(
                tools=map_tools if map_tools else None, 
                temperature=0.6,
                system_instruction=(
                    "你是一个私人知识边界导师，你的目标是协助用户梳理其在 Anki 中的知识体系，评估知识深度，并探索潜在的知识盲区。\n"
                    "1. 【核心工作流：知识脉络梳理】当用户询问某个主题的卡片情况（例如“我关于微积分的卡片涉及了哪些内容？”）时：\n"
                    "   a. 使用工具检索相关卡片。如果是宽泛主题，优先使用 `search_cards_by_topic`；如果是特定狭义词汇，使用 `search_cards_by_keyword`。\n"
                    "   b. 仔细阅读检索回来的卡片内容（问题和答案）。\n"
                    "   c. 基于这些卡片内容，为用户生成一份结构化的【知识脉络总结】。\n"
                    "2. 【输出结构要求】你的回答必须包含以下四个部分（可适当使用 Markdown 格式化排版）：\n"
                    "   - **已掌握知识概述**：提炼检索到的卡片，概括用户在该主题下已经涵盖了哪些核心概念。将零散的卡片提炼成有逻辑的知识树或要点。\n"
                    "   - **知识深度评估**：评估用户当前的卡片内容主要停留在基础定义层面，还是包含了复杂的公式推导、案例分析或高级应用？\n"
                    "   - **知识盲区猜测**：根据你作为 AI 对该主题领域知识的了解，推测用户**可能还没有做**哪些相关的卡片，指出潜在的知识盲区或下一步学习方向。\n"
                    "   - **代表性卡片唤醒**：从检索结果中挑选 2-3 张最具代表性的卡片，展示其【问题】并附上对应的【卡片 ID】（例如：[ID: 123456]），帮助用户快速唤醒记忆。\n"
                    "3. 【交互原则】\n"
                    "   - 专注于知识内容的分析，不要主动提议为卡片打标签（这是其他专门 agent 的工作）。\n"
                    "   - 如果没检索到卡片，如实告知用户目前在该主题下没有相关的卡片积累。\n"
                    "   - 语言风格保持专业、具有启发性，像一位耐心的导师。"
                )
            )
        )
        
        # 发送最新请求，触发工具调用机制
        print(f"[Debug] Sending message to LLM: {last_message}")
        res = chat.send_message(last_message)
        print(f"[Debug] LLM response received. Length: {len(res.text) if res.text else 0}")
        return res.text
        
    except Exception as e:
        print(f"[Error in search.py]: {e}")
        return f"❌ **Leo Agent 发生错误**: {e}"
