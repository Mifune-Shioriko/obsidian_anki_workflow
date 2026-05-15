from google import genai
from google.genai import types
import utils

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

SYSTEM_PROMPT_BASE = """你现在的角色是我的“精读领航员”。
我的学习方法是：先通过略读（Skim）提取教材中的核心概念和结论（骨架）并制作 Anki 卡片复习，然后再去精读教材，补充具体的流程、机制、推导和细节（血肉）。

你接收到的是我今天复习过的卡片内容（可能跨越多个学科，包含宏观概念和部分结论）。
你的核心任务是：不要流水账式地总结！请帮我指明下一步精读的方向，告诉我应该如何为这些骨架“填充血肉”。

考虑到我复习的领域可能很多，请挑选 2-3 个最核心、最成体系或最值得深挖的主题来进行延伸。

请严格按照以下结构输出：

1. 🦴 骨架宏观扫描：
- 用一两句话简要概括我今天搭建的几大核心知识框架（如：今天主要搭建了生物化学的能量代谢框架，以及物理学的静电场基础等）。

2. 🥩 血肉填充指南（精读方向）：
（挑选 2-3 个最值得深挖的主题，指出我在接下来的精读中需要重点寻找的“细节”）
- [主题 A] 的深挖方向：去书中寻找...（例如：底层化学/物理机制、完整的推导过程、上下文联系或经典实验）
- [主题 B] 的深挖方向：去书中留意...（例如：具体的生理反应级联流程、特殊情况与例外、现实应用场景）

3. 🎯 靶向问题（带着问题去阅读）：
- 针对上述推荐的精读方向，提出 3-4 个关于机制、流程或因果关系的具体问题。这些问题绝不能从我提供的卡片中直接找到答案，必须是我深入阅读教材才能解答的。

严格遵守以下输出格式限制：

1. 回答结构：可以使用自然段，bullet points，表格等，请自行选择。
但为了配合上面的结构要求，建议主体部分使用列表结构。

2. bullet points 格式
- 使用 "-" 作为 bullet
- 每行一个 bullet
示例：
- 第一条内容
- 第二条内容

3. 禁止格式
- 不允许使用 **粗体**

4. 数学公式规则
- 行内公式使用 $...$
- 独立公式使用 $$...$$
- $ 或 $$ 与周围文本之间必须有空格
- 行内公式 $ 必须紧贴公式本体

正确示例：
牛顿第二定律 $F=ma$ 描述了力与加速度的关系。

错误示例：
牛顿第二定律$F=ma$描述了力与加速度的关系。
"""

def handle(command, history, note_path, full_content):
    try:
        # 1. 获取今天复习过的卡片 ID ("rated:1" 表示今天复习过的卡片)
        print("正在从 Anki 获取今日复习数据以生成阅读建议...")
        card_ids = utils.anki_request("findCards", {"query": "rated:1"})
        
        if not card_ids:
            return "今天似乎还没有复习任何卡片哦！"
            
        # 2. 获取卡片详情
        cards_info = utils.anki_request("cardsInfo", {"cards": card_ids})
        if not cards_info:
            return "获取复习卡片详情失败。"
            
        # 3. 提取卡片文本内容构建上下文
        review_context = "以下是我今天复习过的卡片内容：\n\n"
        for i, card in enumerate(cards_info):
            # 取出 Front 和 Back 字段的值并替换 <br> 为换行
            q = card.get('fields', {}).get('Front', {}).get('value', '').replace('<br>', '\n')
            a = card.get('fields', {}).get('Back', {}).get('value', '').replace('<br>', '\n')
            
            # 若使用了非默认字段名，容错处理：直接取前两个字段
            if not q and not a:
                 fields = list(card.get('fields', {}).values())
                 if len(fields) >= 1: q = fields[0].get('value', '').replace('<br>', '\n')
                 if len(fields) >= 2: a = fields[1].get('value', '').replace('<br>', '\n')

            review_context += f"【卡片 {i+1}】\nQ: {q}\nA: {a}\n\n"
            
        # 简单截断处理，防止如果今天复习量极大时超出大模型的 Token 限制
        if len(review_context) > 30000:
             review_context = review_context[:30000] + "\n... (内容过长已截断)"
             
        # 4. 组装发给大模型的内容
        prompt_content = [
             types.Content(role="user", parts=[types.Part.from_text(text=review_context)])
        ]
        
        # 5. 调用大模型
        print(f"正在调用 {utils.MODEL_NAME} 生成阅读建议...")
        res = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=prompt_content,
            config=types.GenerateContentConfig(
                temperature=0.7,
                system_instruction=SYSTEM_PROMPT_BASE,
            )
        )
        
        if not res.text:
            return "❌ AI 返回了空内容，可能是触发了安全限制。"
            
        # 6. 使用 utils.sanitize_format 清洗大模型输出
        reply_text = utils.sanitize_format(res.text)
        return reply_text

    except Exception as e:
        return f"❌ **Reading Suggestions Agent 发生错误**: {e}"
