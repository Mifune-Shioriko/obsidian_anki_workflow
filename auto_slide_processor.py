#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import uuid
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import fitz  # PyMuPDF
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# 允许引入同目录下的工具库
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import utils

# 加载环境变量
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
VAULT_DIR = os.getenv("VAULT_DIR")

if not GOOGLE_API_KEY or not VAULT_DIR:
    print("[!] 错误: 未能在环境变量或 .env 中找到 GOOGLE_API_KEY 或 VAULT_DIR。")
    sys.exit(1)

client = genai.Client(api_key=GOOGLE_API_KEY)

# =====================================================================
# Pydantic 结构体定义 (用于 Gemini 3.5 结构化输出)
# =====================================================================
class SlidePageAnalysis(BaseModel):
    page: int = Field(description="页码, 从 1 开始的整数")
    type: str = Field(description="分类类型: 'title_page' (封面), 'outline' (目录), 'transition' (过渡页), 'ending' (结束/致谢页), 'content' (内容页)")
    reason: str = Field(description="简短的判定理由，中文")
    action: str = Field(description="处理动作: 'skip' (跳过) 或 'keep' (保留处理)")

class SlideAnalysisResponse(BaseModel):
    analysis: list[SlidePageAnalysis] = Field(description="所有幻灯片页面的分析结果列表")

# =====================================================================
# 核心业务逻辑
# =====================================================================

def get_next_image_number(files_dir):
    """
    扫描 Files/ 目录，找出最大的 image-{N}.png 编号并返回 N+1
    """
    max_num = 0
    p = re.compile(r"^image-(\d+)\.png$")
    if os.path.exists(files_dir):
        for f in os.listdir(files_dir):
            m = p.match(f)
            if m:
                max_num = max(max_num, int(m.group(1)))
    return max_num + 1

def convert_pptx_to_pdf(pptx_path):
    """
    使用 LibreOffice headless 将 PPTX 转换为 PDF 存放到临时目录
    """
    print(f"[*] 检测到 PPTX 文件，正在使用 LibreOffice 转换为 PDF...")
    temp_dir = "/tmp/opencode"
    os.makedirs(temp_dir, exist_ok=True)
    
    cmd = [
        "soffice",
        "--headless",
        "--convert-to", "pdf",
        "--outdir", temp_dir,
        str(pptx_path)
    ]
    print(f"[*] 执行转换命令: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[!] LibreOffice 转换失败:\n{result.stderr}")
        return None
        
    pdf_name = pptx_path.stem + ".pdf"
    pdf_path = Path(temp_dir) / pdf_name
    if pdf_path.exists():
        print(f"[+] 成功转换 PPTX 到 PDF: {pdf_path}")
        return pdf_path
    else:
        print("[!] 转换未生成预期的 PDF 文件。")
        return None

def analyze_slides(pdf_path):
    """
    使用 PyMuPDF 提取每一页的文本，并让 Gemini-3.5-Flash 进行结构分析，识别内容页和结构页
    """
    print(f"[*] 正在读取 PDF 并进行结构预分析: {pdf_path.name}...")
    doc = fitz.open(pdf_path)
    slides_data = []
    
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text().strip()
        slides_data.append({
            "page": i + 1,
            "text": text[:1500]  # 限制长度以防止极长文本消耗无用 token
        })
        
    print(f"[*] 成功提取 {len(doc)} 页文本，正在向 Gemini 请求结构化分析...")
    
    prompt = f"""你是一个极其专业的学术幻灯片（Slide）结构分析专家。
你的任务是阅读给出的每一页 Slide 的文本内容，分析其在整个幻灯片文件中的结构角色，并决定是否应当跳过该页。

幻灯片页面通常包含以下几种结构：
1. `title_page` (封面/标题页)：通常是第一页，包含课程/报告标题、作者、日期、单位等，无实质学术或考点内容。 -> 【跳过】
2. `outline` (目录/大纲页)：列出本讲/本章大纲、目录、"Contents"、"Outline"、"Agenda" 等，无实质学术或考点内容。 -> 【跳过】
3. `transition` (过渡页/分段页)：只有一两行大字，标明 "Part 1"、"Chapter 2" 或某个大章节标题。 -> 【跳过】
4. `ending` (结束页/致谢页/问答页)：最后一两页，包含 "Thank you"、"Q&A"、"谢谢大家"、"参考文献"、"References" 等，无实质学术或考点内容。 -> 【跳过】
5. `content` (实质内容页)：讲解具体定义、公式、原理、步骤、图表、代码、实验结果等具有学习和记忆价值的实质性内容。 -> 【保留并处理】

请根据上述定义，对传入的每一页 Slide 文本进行分类。
注意：
- 只要页面包含实质性的学术知识点或需要学习的细节，即使包含一些过渡词或带有小标题，也应当归类为 `content` (action 为 'keep')。
- 如果某页文字极少且没有实质内容，可以归类为 `transition` 或相应的跳过类别 (action 为 'skip')。

幻灯片页面数据列表：
{json.dumps(slides_data, ensure_ascii=False, indent=2)}
"""

    try:
        response = client.models.generate_content(
            model=utils.MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=SlideAnalysisResponse,
            )
        )
        parsed_result: SlideAnalysisResponse = response.parsed
        return parsed_result.analysis
    except Exception as e:
        print(f"[!] 结构预分析失败: {e}")
        return None

def display_analysis_and_get_approval(analysis):
    """
    向用户展示分析规划，并交互式获取执行确认或自定义页码
    """
    print("\n" + "="*50)
    print("📋 [Slide 结构预分析完成] 规划如下:")
    print("="*50)
    
    keep_pages = []
    for item in analysis:
        status_char = "🟢 [处理]" if item.action == "keep" else "🔴 [跳过]"
        print(f"  - 第 {item.page:02d} 页: {status_char} (类型: {item.type:<12} | 原因: {item.reason})")
        if item.action == "keep":
            keep_pages.append(item.page)
            
    print("="*50)
    print(f"推荐处理的内容页 (共 {len(keep_pages)} 页): {keep_pages}")
    print("="*50)
    
    while True:
        print("\n请输入操作指令:")
        print("  - [Enter] (回车): 确认推荐规划，开始全自动流水线")
        print("  - 输入特定的页码列表 (英文逗号隔开，如 3,4,6,7): 覆盖推荐，仅处理指定页码")
        print("  - 输入 [q]: 放弃并退出")
        user_input = input(">>> ").strip()
        
        if not user_input:
            return keep_pages
        elif user_input.lower() == 'q':
            print("[*] 用户取消操作。")
            sys.exit(0)
        else:
            # 尝试解析自定义页码
            try:
                custom_pages = [int(p.strip()) for p in user_input.split(",") if p.strip()]
                custom_pages = sorted(list(set(custom_pages)))
                print(f"[*] 自定义处理页码: {custom_pages}")
                return custom_pages
            except ValueError:
                print("[!] 输入格式有误，请输入逗号分隔的整数页码。")

def main():
    if len(sys.argv) < 2:
        print("用法: python auto_slide_processor.py <slide_path_pptx_or_pdf>")
        sys.exit(1)
        
    input_file = Path(sys.argv[1]).resolve()
    if not input_file.exists():
        print(f"[!] 错误: 找不到输入文件 '{input_file}'")
        sys.exit(1)
        
    # 确定真正的 PDF 路径
    if input_file.suffix.lower() == '.pptx':
        pdf_path = convert_pptx_to_pdf(input_file)
        if not pdf_path:
            print("[!] 错误: PPTX 转换 PDF 失败，程序退出。")
            sys.exit(1)
    elif input_file.suffix.lower() == '.pdf':
        pdf_path = input_file
    else:
        print("[!] 错误: 仅支持 .pdf 和 .pptx 格式的幻灯片文件。")
        sys.exit(1)
        
    # 1. 结构预分析
    analysis = analyze_slides(pdf_path)
    if not analysis:
        print("[!] 错误: 无法获取结构预分析结果，全自动流水线终止。")
        sys.exit(1)
        
    # 2. 交互式确认
    target_pages = display_analysis_and_get_approval(analysis)
    if not target_pages:
        print("[-] 没有需要处理的页面，流程结束。")
        sys.exit(0)
        
    # 准备路径和参数
    files_dir = os.path.join(VAULT_DIR, "Files")
    daily_dir = os.path.join(VAULT_DIR, "Daily Notes")
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_path = os.path.join(daily_dir, f"{today_str}.md")
    
    # 确保 Daily Notes 文件夹和今日笔记存在
    os.makedirs(daily_dir, exist_ok=True)
    if not os.path.exists(daily_path):
        with open(daily_path, 'w', encoding='utf-8') as f:
            f.write(f"# {today_str}\n\n")
            
    doc = fitz.open(pdf_path)
    
    print(f"\n🚀 开始全自动流水线处理，总共需要处理 {len(target_pages)} 页...")
    
    # 获取初始图片编号
    next_image_num = get_next_image_number(files_dir)
    
    page_idx_in_list = 0
    while page_idx_in_list < len(target_pages):
        page_num = target_pages[page_idx_in_list]
        idx = page_idx_in_list + 1
        
        # page_num 是从 1 开始的，PDF 索引是从 0 开始的
        page_idx = page_num - 1
        page = doc[page_idx]
        raw_slide_text = page.get_text().strip()
        
        # 净化幻灯片文本：防止其中任何行以 > 开头，从而破坏 Markdown 引用历史解析
        sanitized_lines = []
        for line in raw_slide_text.splitlines():
            if line.strip().startswith('>'):
                sanitized_lines.append(" " + line)
            else:
                sanitized_lines.append(line)
        slide_text = "\n".join(sanitized_lines)
        
        print(f"\n" + "-"*60)
        print(f"🎬 [进度 {idx}/{len(target_pages)}] 正在处理第 {page_num} 页 Slide...")
        print(f"   - 预设分配图片编号: image-{next_image_num}.png")
        print("-"*60)
        
        # 1. 渲染并保存图片
        os.makedirs(files_dir, exist_ok=True)
        image_name = f"image-{next_image_num}.png"
        image_path = os.path.join(files_dir, image_name)
        
        pix = page.get_pixmap(dpi=150)
        pix.save(image_path)
        print(f"[+] Slide 图片成功渲染并保存至: Files/{image_name}")
        
        # 2. 备份今日笔记
        with open(daily_path, 'r', encoding='utf-8') as f:
            daily_backup_content = f.read()
            
        # 3. 清理末尾，将草稿追加到今日笔记
        clean_backup = daily_backup_content.rstrip()
        # 如果原本以分隔符结尾，去掉，稍后统一用新分隔符追加
        if clean_backup.endswith("---"):
            clean_backup = clean_backup[:-3].rstrip()
            
        draft_content = f"""

---

![[{image_name}]]
幻灯片文本：
{slide_text}

> ![[{image_name}]]
> @explain @dig @new
"""
        
        with open(daily_path, 'w', encoding='utf-8') as f:
            f.write(clean_backup + draft_content)
            
        print("[*] 幻灯片临时草稿和多 Agent 指令链已成功挂载到 Daily Note 尾部。")
        print("[*] 正在启动 Multi-Agent 链式管道 (explain ➔ dig ➔ new)...")
        
        # 4. 运行 router 管道机制
        # 使用 subprocess 运行以确保隔离和安全出口
        router_cmd = [sys.executable, "router.py", daily_path]
        router_res = subprocess.run(router_cmd, capture_output=True, text=True)
        
        # 检查是否管道熔断或出错
        if router_res.returncode != 0:
            print(f"[!] 警告: Agent 管道执行失败或已被熔断！")
            print(f"    - 错误信息:\n{router_res.stdout}\n{router_res.stderr}")
        else:
            print("[+] Agent 链式计算成功完成！")
            
        # 5. 人机交互检查
        while True:
            print("\n" + "="*50)
            print(f"🎯 [第 {page_num} 页 Slide 计算完成]")
            print("  请现在打开 Obsidian 检查当日 Daily Note 尾部生成的解释与卡片表！")
            print("="*50)
            print("  [Enter] (回车): 效果完美，将其打包并转换成原子笔记 (Atomic Note)")
            print("  [r] (重试): 还原 Daily Note，删除图片，重新运行这一页（可先修改 Agent 源码或重试）")
            print("  [q] (退出): 还原 Daily Note，删除图片，并安全退出管线")
            print("="*50)
            choice = input("选择操作 >>> ").strip().lower()
            
            if not choice:
                # 确认：调用 daily_to_atomic.py 打包
                print("[*] 正在调用 daily_to_atomic.py 生成卡片盒原子笔记...")
                dt_cmd = [sys.executable, "daily_to_atomic.py"]
                dt_res = subprocess.run(dt_cmd, capture_output=True, text=True)
                
                if dt_res.returncode == 0:
                    print(f"[+] 原子化转换成功！")
                    # 打印 daily_to_atomic 的成果
                    for line in dt_res.stdout.splitlines():
                        if "已生成新卡片" in line or "AI 生成标题" in line:
                            print(f"    {line}")
                    # 处理下一张，图片编号自增，并在 target 列表中往后走
                    next_image_num += 1
                    page_idx_in_list += 1
                    break
                else:
                    print(f"[!] 转换原子笔记失败！\n{dt_res.stdout}\n{dt_res.stderr}")
                    print("[!] 请检查格式。若想放弃本次生成的垃圾内容，请输入 r 进行还原重试。")
                    
            elif choice == 'r':
                # 回滚
                print("[-] 正在回滚 Daily Note，并删除临时渲染图片...")
                with open(daily_path, 'w', encoding='utf-8') as f:
                    f.write(daily_backup_content)
                if os.path.exists(image_path):
                    os.remove(image_path)
                print("[+] 回滚完成。准备重新处理当前 Slide...")
                # 重新跑当前页，不增加 page_idx_in_list 和 next_image_num
                break
                
            elif choice == 'q':
                # 还原并安全退出
                print("[-] 正在还原 Daily Note，并删除临时渲染图片...")
                with open(daily_path, 'w', encoding='utf-8') as f:
                    f.write(daily_backup_content)
                if os.path.exists(image_path):
                    os.remove(image_path)
                print("[+] 状态已恢复，Pipeline 安全退出。祝您学习愉快！")
                sys.exit(0)

    print("\n" + "="*50)
    print("🎉 恭喜！所选幻灯片的所有页面均已全部处理并原子化转换完毕！")
    print("   请运行 `python sync.py` 同步到 Anki，尽情享受复习吧！")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()
