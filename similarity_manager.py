import os
import re
import sys
import json
import uuid
import hashlib
from pathlib import Path
from dotenv import load_dotenv

# Ensure we can import utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import utils

from google import genai
from google.genai import types
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# ================= 配置区域 =================
EMBEDDING_MODEL = "gemini-embedding-2"  # Google 最新最强的 3072 维文本嵌入模型
COLLECTION_NAME = "atomic_notes"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".similarity_cache.json")

# 相似度门槛和最大展示数量
SIMILARITY_THRESHOLD = 0.72
MAX_RELATED_NOTES = 15
# ============================================

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"[*] 警告：加载本地缓存失败 ({e})，将重建缓存。")
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[*] 警告：保存缓存文件失败 ({e})")

def get_sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def extract_id_from_yaml(content):
    pattern = r'^id:\s*["\']?([^"\s\n\']+)["\']?'
    match = re.search(pattern, content, re.MULTILINE)
    return match.group(1).strip() if match else None

def extract_sections(content):
    """
    精确切分 Markdown 文件，返回:
    yaml_frontmatter, pure_body, old_related_section, cards_section
    """
    yaml_frontmatter = ""
    body = content
    
    # 检测 YAML Frontmatter
    if content.startswith("---\n") or content.startswith("---\r\n"):
        parts = re.split(r'^\r?\n---\r?\n', content, maxsplit=1, flags=re.MULTILINE)
        if len(parts) == 2:
            yaml_frontmatter = parts[0] + "\n---\n"
            body = parts[1]
        else:
            idx = content.find("\n---\n", 4)
            if idx != -1:
                yaml_frontmatter = content[:idx+5]
                body = content[idx+5:]

    # 切离卡片区域 (## 卡片)
    cards_match = re.search(r'^##\s+卡片\s*$', body, re.MULTILINE)
    cards_section = ""
    main_body = body
    if cards_match:
        cards_idx = cards_match.start()
        main_body = body[:cards_idx]
        cards_section = body[cards_idx:]

    # 切离相关笔记区域 (## 相关笔记 或 ## Related Notes)
    related_match = re.search(r'^##\s*(?:相关笔记|Related\s+Notes)\s*$', main_body, re.MULTILINE)
    related_section = ""
    pure_body = main_body
    if related_match:
        related_idx = related_match.start()
        pure_body = main_body[:related_idx]
        related_section = main_body[related_idx:]

    return yaml_frontmatter, pure_body.strip(), related_section.strip(), cards_section.strip()

def ensure_note_id(file_path, content):
    """
    确保笔记有稳定的 UUID，若没有则在 frontmatter 中自动注入
    """
    note_id = extract_id_from_yaml(content)
    if note_id:
        return note_id, content
    
    new_id = uuid.uuid4().hex
    
    # 如果已有 YAML frontmatter，直接插入
    if content.startswith("---\n") or content.startswith("---\r\n"):
        lines = content.split('\n')
        inserted = False
        for idx, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                lines.insert(idx, f"id: {new_id}")
                inserted = True
                break
        if inserted:
            new_content = "\n".join(lines)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"[*] 自动为笔记注入 UUID -> {file_path.name} [ID: {new_id}]")
            return new_id, new_content

    # 无 YAML frontmatter，创建全新的
    title = file_path.stem
    new_content = f"---\nid: {new_id}\ntitle: {title}\n---\n" + content
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    print(f"[*] 自动为笔记创建 YAML 并注入 UUID -> {file_path.name} [ID: {new_id}]")
    return new_id, new_content

def get_stable_uuid(note_id):
    """将 note_id 转换为符合 Qdrant 规范的标准 36 位 UUID 字符串"""
    try:
        return str(uuid.UUID(note_id))
    except ValueError:
        # 如果不是标准格式，使用 namespace_dns 派生稳定 UUID
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, note_id))

def main():
    load_dotenv()
    
    vault_root = utils.VAULT_DIR
    if not vault_root or not os.path.exists(vault_root):
        print(f"[!] 错误: 未能在环境变量中找到有效的 VAULT_DIR ({vault_root})")
        sys.exit(1)
        
    target_folder_path = os.path.join(vault_root, "Atomic Notes")
    if not os.path.exists(target_folder_path):
        print(f"[!] 错误: 找不到原子笔记目录: {target_folder_path}")
        sys.exit(1)

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")

    print(f"[*] 启动语义关联引擎...")
    print(f"[*] 笔记库根目录: {vault_root}")
    print(f"[*] 正在连接 Qdrant 服务 ({qdrant_url}) ...")
    
    try:
        qdrant = QdrantClient(url=qdrant_url)
        # 测试连接
        collections_res = qdrant.get_collections()
        existing_collections = [c.name for c in collections_res.collections]
    except Exception as e:
        print(f"[!] 错误: 无法连接到 Qdrant 数据库。请检查 Docker 容器是否正常运行，接口是否受限。")
        print(f"[!] 错误明细: {e}")
        sys.exit(1)

    # 自动创建 notes 集合
    if COLLECTION_NAME not in existing_collections:
        print(f"[*] 创建 Qdrant 集合 '{COLLECTION_NAME}' (维度: 3072, 距离度量: Cosine) ...")
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=3072, distance=Distance.COSINE)
        )
    else:
        # 校验维度是否匹配，若不匹配（如旧的 768 维度），进行自愈重建
        try:
            coll_info = qdrant.get_collection(collection_name=COLLECTION_NAME)
            current_size = coll_info.config.params.vectors.size
            if current_size != 3072:
                print(f"[*] 检测到 Qdrant 集合维度不匹配 ({current_size} != 3072)，正在重建集合...")
                qdrant.delete_collection(collection_name=COLLECTION_NAME)
                qdrant.create_collection(
                    collection_name=COLLECTION_NAME,
                    vectors_config=VectorParams(size=3072, distance=Distance.COSINE)
                )
        except Exception as e:
            print(f"[*] 警告: 校验/重建 Qdrant 集合时发生异常: {e}")

    print(f"[*] 正在初始化 Google GenAI 客户端...")
    client = genai.Client(api_key=utils.GOOGLE_API_KEY)

    # 加载本地缓存
    cache = load_cache()
    
    # 扫描所有笔记文件
    all_files = list(Path(target_folder_path).rglob("*.md"))
    print(f"[*] 扫描到 {len(all_files)} 篇原子笔记，正在进行增量比对...")

    active_note_ids = set()
    note_details = {}  # yaml_id -> { "file_path": Path, "title": str, "pure_body": str, "vector": list }
    cache_updated = False

    # 1. 检测修改/新增，并计算向量
    for file_path in all_files:
        try:
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                content = f.read()
        except Exception as e:
            print(f"[!] 警告: 无法读取文件 {file_path}: {e}")
            continue

        # 确保有 YAML ID
        yaml_id, content = ensure_note_id(file_path, content)
        active_note_ids.add(yaml_id)
        
        # 提取纯净正文
        yaml_frontmatter, pure_body, _, _ = extract_sections(content)
        pure_hash = get_sha256(pure_body)
        
        # 获取文件名（不含扩展名）作为标题
        title = file_path.stem
        relative_path = str(file_path.relative_to(target_folder_path))
        mtime = os.path.getmtime(file_path)

        # 比对缓存
        cached_info = cache.get(yaml_id)
        need_embedding = True
        vector = None

        if cached_info:
            # 路径相同、内容未变、且 Qdrant 中已有
            if (cached_info.get("hash") == pure_hash and 
                cached_info.get("relative_path") == relative_path):
                need_embedding = False
                # 尝试从 Qdrant 直接获取向量（避免本地缓存过大），若获取失败再重新 embed
                try:
                    point_uuid = get_stable_uuid(yaml_id)
                    retrieved = qdrant.retrieve(
                        collection_name=COLLECTION_NAME,
                        ids=[point_uuid],
                        with_vectors=True
                    )
                    if retrieved and retrieved[0].vector:
                        vector = retrieved[0].vector
                    else:
                        need_embedding = True
                except Exception:
                    need_embedding = True

        # 如果需要重新计算向量
        if need_embedding:
            # 如果纯文本内容为空，给一个默认占位文本以生成向量
            embed_text = pure_body if pure_body.strip() else title
            print(f"📤 正在为笔记生成向量 -> {title}")
            try:
                embed_res = client.models.embed_content(
                    model=EMBEDDING_MODEL,
                    contents=embed_text
                )
                vector = embed_res.embeddings[0].values
                point_uuid = get_stable_uuid(yaml_id)
                
                # 写入 Qdrant
                qdrant.upsert(
                    collection_name=COLLECTION_NAME,
                    points=[
                        PointStruct(
                            id=point_uuid,
                            vector=vector,
                            payload={
                                "note_id": yaml_id,
                                "title": title,
                                "relative_path": relative_path
                            }
                        )
                    ]
                )
                
                # 更新本地缓存
                cache[yaml_id] = {
                    "hash": pure_hash,
                    "relative_path": relative_path,
                    "title": title,
                    "mtime": mtime
                }
                cache_updated = True
                save_cache(cache)
            except Exception as e:
                print(f"[!] 错误: 笔记 {title} 生成或上传向量失败: {e}")
                continue
        else:
            # 如果路径/标题发生变动，直接更新 Qdrant 负载，不需要重新计算向量
            if (cached_info.get("relative_path") != relative_path or 
                cached_info.get("title") != title):
                print(f"🔄 检测到文件位置或命名改变，更新负载 -> {title}")
                try:
                    point_uuid = get_stable_uuid(yaml_id)
                    qdrant.set_payload(
                        collection_name=COLLECTION_NAME,
                        payload={
                            "relative_path": relative_path,
                            "title": title
                        },
                        points=[point_uuid]
                    )
                    cache[yaml_id]["relative_path"] = relative_path
                    cache[yaml_id]["title"] = title
                    cache[yaml_id]["mtime"] = mtime
                    cache_updated = True
                    save_cache(cache)
                except Exception as e:
                    print(f"[!] 警告: 更新 Qdrant 负载失败: {e}")

        # 记录笔记详情供下一步搜索使用
        note_details[yaml_id] = {
            "file_path": file_path,
            "title": title,
            "pure_body": pure_body,
            "vector": vector
        }

    # 2. 清理已被用户删除的笔记（Qdrant + 缓存）
    cached_ids = list(cache.keys())
    deleted_ids = []
    for cached_id in cached_ids:
        if cached_id not in active_note_ids:
            point_uuid = get_stable_uuid(cached_id)
            deleted_ids.append(point_uuid)
            print(f"🗑️ 检测到笔记已被物理删除，正在从 Qdrant 清除 -> {cache[cached_id].get('title', cached_id)}")
            del cache[cached_id]
            cache_updated = True

    if deleted_ids:
        try:
            qdrant.delete(
                collection_name=COLLECTION_NAME,
                points_selector=deleted_ids
            )
        except Exception as e:
            print(f"[!] 警告: 从 Qdrant 批量删除孤儿节点失败: {e}")

    # 保存最新缓存
    if cache_updated:
        save_cache(cache)

    # 3. 计算相似度并写入 Markdown
    print(f"[*] 正在为所有笔记检索相似节点...")
    files_modified_count = 0

    for yaml_id, detail in note_details.items():
        vector = detail["vector"]
        file_path = detail["file_path"]
        title = detail["title"]
        pure_body = detail["pure_body"]
        point_uuid = get_stable_uuid(yaml_id)

        if not vector:
            continue

        try:
            # 查询与当前笔记最相似的节点 (因为需要排除自身，所以 limit 设为 MAX+1)
            search_res = qdrant.query_points(
                collection_name=COLLECTION_NAME,
                query=vector,
                limit=MAX_RELATED_NOTES + 1,
                score_threshold=SIMILARITY_THRESHOLD
            )
        except Exception as e:
            print(f"[!] 警告: 检索笔记 '{title}' 的相似项失败: {e}")
            continue

        related_links = []
        for hit in search_res.points:
            hit_id = hit.payload.get("note_id")
            hit_title = hit.payload.get("title")
            # 排除自身
            if hit_id == yaml_id:
                continue
            # 过滤掉不存在于本次扫描中的无主节点 (幽灵节点)
            if hit_id not in active_note_ids:
                continue
            
            related_links.append(f"- [[{hit_title}]]")
            if len(related_links) >= MAX_RELATED_NOTES:
                break

        # 读取文件的最新完整内容
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue

        yaml_frontmatter, pure_body, old_related, cards_section = extract_sections(content)

        # 构造新的相关笔记栏目
        new_related_section = ""
        if related_links:
            new_related_section = "## 相关笔记\n\n" + "\n".join(related_links)

        # 对比是否有变化（包括内容变化和原相关笔记栏目内容变化）
        old_related_normalized = old_related.strip().replace("\r\n", "\n")
        new_related_normalized = new_related_section.strip()

        if old_related_normalized != new_related_normalized:
            # 重新组装文件
            new_content = yaml_frontmatter
            if pure_body:
                new_content += pure_body + "\n\n"
            
            if new_related_section:
                new_content += new_related_section + "\n\n"
                
            if cards_section:
                new_content += cards_section + "\n"
            
            # 移除尾部多余空白
            new_content = new_content.rstrip() + "\n"

            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                files_modified_count += 1
            except Exception as e:
                print(f"[!] 错误: 写入文件失败 {file_path}: {e}")

    print(f"\n============================================")
    print(f"[+] 语义相似度计算完毕！")
    print(f"    - 全局扫描笔记: {len(active_note_ids)} 篇")
    print(f"    - 更新了相似链的笔记数: {files_modified_count} 篇")
    print("============================================\n")

if __name__ == "__main__":
    main()
