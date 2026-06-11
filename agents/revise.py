from pydantic import BaseModel, Field
import model_client as genai
from model_client import types
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
    # ================= 1. 确认写入逻辑（保持不变） =================
    if command.lower() in ['ok', 'y', 'yes', '确认']:
        draft_cards = []
        for turn in reversed(history):
            if turn['role'] == 'model':
                draft_cards = utils.extract_cards_from_table_text(turn['parts'][0]['text'])
                if draft_cards:
                    break

        if not draft_cards:
            return "⚠️ **Revise Agent 提醒**：未在上面的聊天记录中找到草稿表格，请重新提出修改要求。"

        current_cards_dict = utils.parse_markdown_table(full_content)

        # 尝试寻找草稿中是否有保留的原卡片 ID
        target_note_id = next((c['id'] for c in draft_cards if c['id'].strip() != ""), None)

        if target_note_id:
            # 场景 1：如果存在原卡片 ID，执行精准替换/插入
            new_full_cards = []
            for c in current_cards_dict:
                if c['id'] == target_note_id:
                    new_full_cards.extend(draft_cards)
                else:
                    new_full_cards.append(c)
            current_cards_dict = new_full_cards
        else:
            # 场景 2：如果草稿全是没有 ID 的新卡（纯添加或拆分），直接追加到底部
            current_cards_dict.extend(draft_cards)

        # 统一写入文件
        utils.rewrite_markdown_table(note_path, full_content, current_cards_dict)
        return "✅ **Revise Agent 执行完毕**：已确认无误，卡片数据已成功更新/追加到底部笔记表格！"

    # ================= 2. 生成草稿逻辑（已优化） =================
    # 尝试获取 Anki 上下文，但不强求（取消了硬拦截）
    anki_note_id = utils.get_current_anki_context()
    current_cards_dict = utils.parse_markdown_table(full_content)
    target_card = None

    if anki_note_id:
        target_card = next((c for c in current_cards_dict if c['id'] == anki_note_id), None)
        # 如果 Anki 传来了 ID，但在笔记里没找到，依然给个提示防呆
        if not target_card:
            return f"⚠️ **Revise Agent 提醒**：在当前笔记的表格中未找到 ID 为 {anki_note_id} 的卡片。请检查光标是否在正确的文档中。"

    # 稍微调整系统提示词，明确“纯添加”的场景
    system_instruction = (
        "你是一个极其严谨的 Anki 卡片重构专家。结合历史对话上下文，处理用户的最新指令。\n"
        "任务规则：\n"
        "1. 单纯修改原卡：原样保留该卡片的 `id`。\n"
        "2. 拆分原卡片：删除原卡片，生成多张新卡片，新卡片 `id` 必须留空字符串 `\"\"`。\n"
        "3. 纯粹添加新卡：直接生成新卡片，新卡片 `id` 必须留空字符串 `\"\"`。\n"
        "4. 补充新卡片（基于原卡）：必须在返回列表中首先原样保留当前卡片（`id` 不变），然后追加新卡片（`id` 留空 `\"\"`）。"
    )

    last_user_idx = len(history) - 1
    original_text = history[last_user_idx]["parts"][0]["text"]

    # 核心改动：根据是否获取到 target_card 动态向大模型注入上下文
    if target_card:
        history[last_user_idx]["parts"][0]["text"] = f"{original_text}\n\n[系统注入] 目标操作卡片当前状态：{target_card}"
    else:
        history[last_user_idx]["parts"][0]["text"] = f"{original_text}\n\n[系统注入] 当前未处于 Anki 复习模式（无关联原卡片）。请直接根据用户指令生成全新卡片，所有新卡片的 id 必须为空字符串 \"\"。"

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
        for card in parsed.cards:
            card.question = utils.unbold_text(card.question)
            card.answer = utils.unbold_text(card.answer)
        draft_table = utils.format_cards_to_table(parsed.cards)

        return f"🤖 **草稿预览**: {parsed.message}\n\n{draft_table}\n\n*(提示：如果您满意该结果，请在下方回复 `> @revise ok` 进行确认并写入；如果不满意，请继续 @revise 提出修改建议。)*"

    except Exception as e:
        return f"❌ **Revise Agent 发生错误**: {e}"
