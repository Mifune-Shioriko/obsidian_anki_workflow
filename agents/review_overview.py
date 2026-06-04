from google import genai
from google.genai import types
import utils

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

# 格式限制与 previous/answer.py 保持完全一致，只是修改了任务描述
SYSTEM_PROMPT_BASE = """你现在的任务是总结我今天复习了哪些卡片。
你接收到的是我今天在 Anki 中复习过的卡片内容（包括问题和答案）。
请根据这些内容，自由发挥生成一个比较详细的概述，帮我梳理知识脉络并回顾今天学到的知识点。

严格遵守以下输出格式限制：

1. 回答结构：可以使用自然段，bullet points，表格等，请自行选择。

2. bullet points 格式
- 使用 "-" 作为 bullet
- 每行一个 bullet
示例：
- 第一条内容
- 第二条内容

3. 数学公式规则
- 行内公式使用 $...$
- 独立公式使用 $$...$$
- $ 或 $$ 与周围文本之间必须有空格
- 行内公式 $ 必须紧贴公式本体

正确示例：
牛顿第二定律 $F=ma$ 描述了力与加速度的关系。

错误示例：
牛顿第二定律$F=ma$描述了力与加速度的关系。

另一个错误示例：
牛顿第二定律 $ F=ma $ 描述了力与加速度的关系。
"""

def handle(command, history, note_path, full_content):
    try:
        # 1. 获取今天复习过的卡片 ID ("rated:1" 表示今天复习过的卡片)
        print("正在从 Anki 获取今日复习数据...")
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
        # 使用 types.Content 模拟一条用户的输入
        prompt_content = [
             types.Content(role="user", parts=[types.Part.from_text(text=review_context)])
        ]
        
        # 5. 调用大模型
        print(f"正在调用 {utils.MODEL_NAME} 生成今日复习总结...")
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
            
        # 6. 使用 utils.sanitize_format 清洗大模型输出（与 answer.py 中一样处理格式）
        reply_text = utils.sanitize_format(res.text)
        return reply_text

    except Exception as e:
        return f"❌ **Review Agent 发生错误**: {e}"
