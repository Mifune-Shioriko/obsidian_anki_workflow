from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import utils

client = genai.Client(api_key=utils.GOOGLE_API_KEY)

class Card(BaseModel):
    question: str = Field(description="卡片的问题部分")
    answer: str = Field(description="卡片的答案部分")
    id: str = Field(description="卡片的 Anki ID。新卡片必须为空字符串。原卡片保留原 ID。")

class DoctorResponse(BaseModel):
    message: str = Field(description="对用户的回复，例如解释做了哪些修改。")
    cards: list[Card] = Field(description="卡片列表（包含修改后的原卡片，以及可能新增的卡片）")

def handle(command, history, note_path, full_content):
    # ================= 1. 确认写入逻辑 =================
    if command.lower() in ['ok', 'y', 'yes', '确认']:
        draft_cards = []
        for turn in reversed(history):
            if turn['role'] == 'model':
                draft_cards = utils.extract_cards_from_table_text(turn['parts'][0]['text'])
                if draft_cards:
                    break

        if not draft_cards:
            return "⚠️ **Add Agent 提醒**：未在上面的聊天记录中找到草稿表格，请重新提出加卡要求。"

        current_cards_dict = utils.parse_markdown_table(full_content)

        # Add Agent 永远是纯添加新卡，直接追加到底部
        current_cards_dict.extend(draft_cards)

        # 统一写入文件
        utils.rewrite_markdown_table(note_path, full_content, current_cards_dict)
        return "✅ **Add Agent 执行完毕**：已确认无误，新卡片已成功追加到底部笔记表格！"

    # ================= 2. 生成草稿逻辑 =================
    current_cards_dict = utils.parse_markdown_table(full_content)
    
    _, context_header, _, _ = utils.parse_document(full_content)
    
    system_instruction = (
        "你是一个极其严谨的 Anki 卡片制作专家。请结合用户的笔记内容和历史对话，纯粹地生成全新的卡片。\n"
        "任务规则：\n"
        "1. 纯粹添加新卡：直接生成新卡片，所有新卡片 `id` 必须留空字符串 `\"\"`。\n"
        "2. 不要假设在修改任何旧卡片，只做全新生成。"
    )
    
    if context_header.strip():
        clean_header = context_header.strip()
        if clean_header.endswith("---"):
            clean_header = clean_header[:-3].strip()
        if clean_header:
            system_instruction += f"\n\n请参考以下当前笔记的正文内容来辅助回答：\n{clean_header}"

    last_user_idx = len(history) - 1
    original_text = history[last_user_idx]["parts"][0]["text"]

    history[last_user_idx]["parts"][0]["text"] = f"{original_text}\n\n[系统注入] 请直接根据用户指令和笔记上下文生成全新卡片，所有新卡片的 id 必须为空字符串 \"\"。"

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
        draft_table = utils.format_cards_to_table(parsed.cards)

        return f"🤖 **草稿预览**: {parsed.message}\n\n{draft_table}\n\n*(提示：如果您满意该结果，请在下方回复 `> @add ok` 进行确认并写入；如果不满意，请继续 @add 提出修改建议。)*"

    except Exception as e:
        return f"❌ **Add Agent 发生错误**: {e}"
