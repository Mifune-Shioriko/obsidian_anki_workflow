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
    print(f"⚠️ Warning: 加载 agent_tools 失败，PubMed 的工具将不可用。错误明细: {e}")
    agent_tools = []

import httpx

# 仅保留基础长超时设置，去除多余的底层改造，保持架构简单稳定
# 注意：这里的 60.0 秒是留给 Google 大模型生成长文本的等待时间（LLM思考往往需要二三十秒），并不是给 NCBI 检索的。
custom_httpx_client = httpx.Client(timeout=60.0, http2=False)
client = genai.Client(
    api_key=utils.GOOGLE_API_KEY,
    http_options={'httpx_client': custom_httpx_client}
)

def handle(command, history, note_path, full_content):
    global client
    try:
        # 分离历史记录与当前提问
        chat_history = history[:-1]
        
        # 安全地提取 last_message
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

        # 提取 pubmed 相关的工具
        pubmed_tools = [tool for tool in agent_tools if 'pubmed' in tool.__name__ or 'web' in tool.__name__]

        system_instruction = (
            "你是一个极其严谨的医学研究助手。请必须严格遵循以下原则：\n"
            "1. 【组合检索策略】：当遇到不熟悉的专有名词、网红医生（如 Paul Saladino）或特定的边缘理论时，请**先使用 `search_web` 工具**在普通网页检索其具体主张。明确了其具体主张或对应的医学名词后，**再提取医学关键词使用 `search_pubmed` 工具**进行严谨验证。\n"
            "2. 【医学验证】：即使网页搜索给出了答案，也**必须**使用 `search_pubmed` 检索学术文献以验证其科学性。不能仅凭网页搜索结果下定论。\n"
            "3. 【承认无知】：如果工具返回的结果是'未找到文献'，你必须直接告知用户'经过检索，未能找到足够的证据来回答这个问题'，**绝对不允许**利用预训练知识捏造任何论文、结论或 PMID 编号。\n"
            "4. 【展示搜索词】：在回答的开头，明确告知用户你分别使用了哪些网页检索关键词和 PubMed 检索关键词。\n"
            "5. 【基于证据】：最终回答必须基于 PubMed 等工具返回的证据。如果文献无相关支持，直接说明现有医学界无相关证据支持该理论。\n"
            "6. 【强制引用】：在提及 PubMed 文献结论时，必须在句子末尾标注引用及超链接。例如：[PMID: 12345678](https://pubmed.ncbi.nlm.nih.gov/12345678/)。\n"
            "7. 【格式限制】：严禁使用 Markdown 粗体语法。\n"
        )

        # 初始化 Chat 会话
        chat = client.chats.create(
            model=utils.MODEL_NAME,
            history=formatted_history, 
            config=types.GenerateContentConfig(
                tools=pubmed_tools if pubmed_tools else None, 
                temperature=0.4, # 降低温度以保证严谨性
                system_instruction=system_instruction
            )
        )
        
        # 发送最新请求，触发工具调用机制，加入重试逻辑防网络闪断
        print(f"[Debug] Sending message to LLM (PubMed Agent): {last_message}")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    chat = client.chats.create(
                        model=utils.MODEL_NAME,
                        history=formatted_history, 
                        config=types.GenerateContentConfig(
                            tools=pubmed_tools if pubmed_tools else None, 
                            temperature=0.4,
                            system_instruction=system_instruction
                        )
                    )
                res = chat.send_message(last_message)
                print(f"[Debug] LLM response received. Length: {len(res.text) if res.text else 0}")
                if not res.text:
                    if res.candidates and len(res.candidates) > 0:
                        finish_reason = getattr(res.candidates[0], "finish_reason", "UNKNOWN")
                        print(f"[Debug] Response has no text. Finish reason: {finish_reason}, Candidates: {res.candidates}")
                        return f"⚠️ **提示**: 大模型拒绝或未能返回有效文本。模型给出的终止原因 (Finish Reason) 为: {finish_reason}。这通常是因为检索内容触发了安全过滤（如 RECITATION / SAFETY）或是网络极其不稳定。"
                    else:
                        print(f"[Debug] Response has no text and no candidates. Raw response: {res}")
                        return "⚠️ **提示**: 大模型返回了空数据包且无候选项。这可能是由于网络代理在中途截断了响应数据。"
                return res.text
            except Exception as e:
                error_str = str(e)
                if attempt < max_retries - 1:
                    print(f"⚠️ PubMed Agent 网络请求失败 ({attempt + 1}/{max_retries})，正在重试... 错误: {error_str.splitlines()[-1] if error_str.splitlines() else error_str}")
                    import time
                    time.sleep(3)
                    continue
                else:
                    raise e
        
    except Exception as e:
        print(f"[Error in pubmed.py]: {e}")
        import traceback
        traceback.print_exc()
        return f"❌ **PubMed Agent 发生错误**: {e}"