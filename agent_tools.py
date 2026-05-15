import os
import requests

import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

class CustomHttpAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context()
        try:
            ctx.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        except AttributeError:
            pass
        pool_kwargs['ssl_context'] = ctx
        super().init_poolmanager(connections, maxsize, block, **pool_kwargs)

http_session = requests.Session()
retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
http_session.mount("https://", CustomHttpAdapter(max_retries=retries))
http_session.mount("http://", CustomHttpAdapter(max_retries=retries))

from google import genai
try:
    from qdrant_client import QdrantClient, models
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

from google.genai import types
from dotenv import load_dotenv, find_dotenv

# find_dotenv() 会自动从当前脚本所在目录出发，一层层往上级目录寻找 .env 文件
load_dotenv(find_dotenv())

# --- 配置区域 ---
ANKI_URL = os.getenv("ANKI_URL", "http://127.0.0.1:8765")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "anki_cards"

# 初始化客户端
ai_client = genai.Client(api_key=GOOGLE_API_KEY)
qdrant = QdrantClient(url=QDRANT_URL) if QDRANT_AVAILABLE else None

# --- 底层辅助函数 ---
def anki_request(action: str, params: dict = None):
    """封装 AnkiConnect 的基础请求"""
    payload = {"action": action, "version": 6, "params": params or {}}
    try:
        res = requests.post(ANKI_URL, json=payload).json()
        if res.get('error'):
            print(f"[Anki Error] {res['error']}")
        return res.get('result')
    except Exception as e:
        print(f"[Anki Connect Error] 无法连接到 Anki: {e}")
        return None

def get_embedding(text: str) -> list[float]:
    """调用 Gemini 生成文本向量 (必须与灌库时使用的模型一致)"""
    response = ai_client.models.embed_content(
        model="gemini-embedding-001", 
        contents=text
    )
    return response.embeddings[0].values

# --- 提供给 Agent 的 工具函数 (Tools) ---
# 注意：函数名、参数类型声明 (Type Hints) 和 Docstring 会被原封不动地发给 LLM，
# LLM 完全依赖这些注释来决定“什么时候调用”以及“怎么调用”这个工具。


def search_new_cards() -> str:
    """
    获取 Anki 中新卡片（未学习）的列表和内容。
    用于回答“我有哪些新卡片？”以及在“新卡片”中检索/筛选。
    获取后，由大模型根据卡片内容直接回答，不使用 RAG。
    """
    card_ids = anki_request("findCards", {"query": "is:new"})
    if not card_ids:
        return "当前没有任何新卡片。"
    
    # 利用大上下文，我们可以放宽限制
    SAFE_LIMIT = 2000 
    sample_ids = card_ids[:SAFE_LIMIT] 
    
    cards_info = anki_request("cardsInfo", {"cards": sample_ids})
    
    if not cards_info:
        return "获取新卡片内容失败。"
        
    result = f"共找到 {len(card_ids)} 张新卡片。\n"
    if len(card_ids) > SAFE_LIMIT:
        result += f"卡片数量较多，已为您提取前 {SAFE_LIMIT} 张卡片的内容供大模型分析：\n\n"
    else:
        result += "以下是所有新卡片的内容清单，请根据用户要求进行匹配和分析：\n\n"
    
    for card in cards_info:
        fields = card.get('fields', {})
        front = fields.get('问题', {}).get('value') or fields.get('Front', {}).get('value') or '未知问题'
        back = fields.get('答案', {}).get('value') or fields.get('Back', {}).get('value') or '未知答案'
        result += f"- 卡片 ID: {card['cardId']} | 问题: {front} | 答案: {back}\n"
    
    return result

def search_cards_by_keyword(keyword: str, only_new: bool = False, limit: int = 500) -> str:
    """
    根据关键词在所有 Anki 卡片中进行精确文本检索。
    非常适合检索特定专业名词、精确匹配。
    如果 only_new=True，则只会检索未学习的新卡片。
    """
    query = f'"{keyword}"'
    if only_new:
        query = f'is:new {query}'
        
    card_ids = anki_request("findCards", {"query": query})
    
    if not card_ids:
        return f"没有找到包含关键词 '{keyword}' 的卡片。"
        
    sample_ids = card_ids[:limit]
    cards_info = anki_request("cardsInfo", {"cards": sample_ids})
    
    if not cards_info:
        return "获取卡片内容失败。"
        
    result = f"包含关键词 '{keyword}' 的卡片共 {len(card_ids)} 张。\n"
    if len(card_ids) > limit:
        result += f"已为您提取前 {limit} 张卡片的内容供分析：\n\n"
    else:
        result += "以下是所有匹配卡片的内容：\n\n"
        
    for card in cards_info:
        fields = card.get('fields', {})
        front = fields.get('问题', {}).get('value') or fields.get('Front', {}).get('value') or '未知问题'
        back = fields.get('答案', {}).get('value') or fields.get('Back', {}).get('value') or '未知答案'
        result += f"- 卡片 ID: {card['cardId']} | 问题: {front} | 答案: {back}\n"
        
    return result

def search_cards_by_topic(topic_query: str, only_new: bool = False, limit: int = 500) -> str:
    """
    根据核心主题词检索相关的 Anki 卡片。
    支持通过 only_new 参数限定仅在新卡片中进行语义检索。
    
    Args:
        topic_query: 检索的关键词或核心主题。(例如："物理学")
        only_new: 是否仅在未学习的新卡片中检索。
        limit: 需要返回的卡片最大数量，默认为 500。
    """
    try:
        if not QDRANT_AVAILABLE:
            return "检索向量数据库失败：未安装 qdrant_client 库，请先运行 pip install qdrant-client"

        # 0. 检查是否限定新卡片并构建过滤器
        query_filter = None
        if only_new:
            new_card_ids = anki_request("findCards", {"query": "is:new"})
            if not new_card_ids:
                return "当前没有任何新卡片可供检索。"
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="card_id",
                        match=models.MatchAny(any=new_card_ids)
                    )
                ]
            )

        # 1. 向量检索：先去 Qdrant 捞取相关的卡片
        query_vector = get_embedding(topic_query)
        
        search_result = qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            score_threshold=0.3 
        )

        if not search_result.points:
            return f"数据库中没有找到与核心主题 '{topic_query}' 足够相关的卡片。"

        # 2. 提取命中的卡片 ID
        hit_cards = []
        for hit in search_result.points:
            card_id = hit.payload.get('card_id')
            if not card_id: continue
            
            hit_cards.append({
                "card_id": int(card_id),
                "question": hit.payload.get('question'),
                "answer": hit.payload.get('answer'),
                "score": hit.score
            })

        # 3. 组装返回给大模型的最终文本
        result_text = f"找到了 {len(hit_cards)} 张关于 '{topic_query}' 的相关卡片：\n\n"
        
        for hit in hit_cards:
            result_text += f"ID: {hit['card_id']}\n问题: {hit['question']}\n答案: {hit['answer']}\n(相关度: {hit['score']:.2f})\n---\n"

        return result_text
        
    except Exception as e:
        print(f"\n[Debug] 向量检索底层报错: {e}")
        return f"检索向量数据库时发生错误: {e}"


def add_tag_to_cards(card_ids: list[int], tag_name: str) -> str:
    """
    为指定的 Anki 卡片批量打上标签。
    参数 card_ids 必须是一个包含 Card ID 的整数列表，例如 [1771349739005, 1771349739010]。
    """
    # 1. 先将 Card ID 转换为 Note ID
    note_ids = anki_request("cardsToNotes", {"cards": card_ids})
    if not note_ids:
        return "打标签失败：无法将卡片 ID 转换为笔记 ID。请检查 ID 是否正确。"
        
    # 2. 调用 addTags 为 Note 打标签
    anki_request("addTags", {"notes": note_ids, "tags": tag_name})
    return f"执行完毕。已成功为 {len(note_ids)} 个笔记（对应这些卡片）打上标签 '{tag_name}'。"

def search_pubmed(query: str, max_results: int = 5) -> str:
    """
    在 PubMed 医学文献数据库中检索相关文献。
    输入搜索词（建议使用英文关键词组合，如 "Vitamin D COVID-19"），返回最相关的文献标题、作者、PMID 和摘要。
    你可以根据这些摘要来回答用户的医学问题。
    """
    try:
        # 1. 搜索相关的 PMID
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": max_results,
            "sort": "relevance",
            "tool": "obsidian_anki_assistant",
            "email": "dummy@example.com"
        }
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = http_session.get(esearch_url, params=params, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()
        id_list = data.get("esearchresult", {}).get("idlist", [])
        
        if not id_list:
            return f"检索完成，但未找到与 '{query}' 相关的 PubMed 文献。系统指令：请立即停止检索，直接回复用户“未能找到相关文献”，绝不能捏造任何论文或结果。"
        
        # 2. 拉取详细的摘要文本
        efetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(id_list),
            "retmode": "text",
            "rettype": "abstract",
            "tool": "obsidian_anki_assistant",
            "email": "dummy@example.com"
        }
        fetch_res = http_session.get(efetch_url, params=fetch_params, headers=headers, timeout=10)
        fetch_res.raise_for_status()
        
        # 3. 组装返回结果
        result = f"为您找到 {len(id_list)} 篇关于 '{query}' 的文献：\n\n"
        result += fetch_res.text
        print(f"[Debug] PubMed tool successfully fetched {len(id_list)} articles, returning {len(result)} chars to LLM.")
        return result
    except Exception as e:
        error_msg = f"检索 PubMed 时发生网络或解析错误: {str(e)}。建议更换关键词或稍后重试。"
        print(f"[Debug] PubMed Search Error: {error_msg}")
        return error_msg

def search_web(query: str, max_results: int = 3) -> str:
    """
    在互联网上进行普通网页搜索。
    当用户询问某个特定的网红、概念、理论或不熟悉的事物时，如果不知道其具体主张，先使用此工具搜索背景信息。
    提取出具体的理论内容后，再用专业数据库去验证。
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
    except ImportError:
        return "网页搜索工具出错: 缺少依赖，请运行 pip install duckduckgo_search"

    print(f"[Debug] Web Search called for: {query}")
    try:
        with DDGS() as ddgs:
            # 兼容不同版本的 duckduckgo_search
            results_iter = ddgs.text(query, max_results=max_results)
            results = list(results_iter) if results_iter else []
            if not results:
                return f"未找到关于 '{query}' 的网页信息。"
            
            output = f"为您找到关于 '{query}' 的 {len(results)} 个网页结果：\n\n"
            for r in results:
                output += f"标题: {r.get('title')}\n链接: {r.get('href')}\n摘要: {r.get('body')}\n\n"
            return output
    except Exception as e:
        return f"网页搜索工具出错: {e}"

# 将工具打包，准备在主程序中传给大模型
agent_tools = [search_new_cards, search_cards_by_topic, search_cards_by_keyword, add_tag_to_cards, search_pubmed, search_web]