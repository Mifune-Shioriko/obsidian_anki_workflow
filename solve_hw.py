import os
import sys
import argparse
import subprocess
import tempfile
import time
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import fitz  # PyMuPDF
import json

# Ensure we can import from the current directory
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GOOGLE_API_KEY:
    print("[!] Error: GOOGLE_API_KEY not found in .env file.")
    sys.exit(1)

CACHE_DIR = os.path.expanduser("~/.cache/hw_solver/")
TARGET_DIR = os.path.expanduser("~/Share/School/hw_submission/")

SYSTEM_PROMPT = """You write final homework solutions in the style of a concise physics/math submission.

Style target:
- Match the Homework 5 style: subpart label, "Solution:", core formulas, substitutions, final result.
- Keep only necessary definitions, equations, substitutions, assumptions, directions, and final answers.
- Use short phrases only when needed to state an assumption, direction, or conclusion.
- Do not write tutorial prose, problem restatements, "Step 1", "To solve this", or "We can see that".
- Do not explain obvious algebra.
- Prefer compact equation chains over paragraphs.
- Each subpart should usually be 2-8 lines unless the derivation truly requires more.
- Output strictly in standard Markdown with standard LaTeX ($x$ inline, $$x$$ block).
"""

COMPRESS_SYSTEM_PROMPT = """You rewrite homework solutions into a concise final submission style.

Rules:
- Preserve all mathematical content and final answers.
- Remove tutorial prose, redundant explanations, and problem restatements.
- Keep only definitions, formulas, substitutions, short necessary conclusions, and final results.
- Keep the original subpart structure.
- Output strictly in standard Markdown with standard LaTeX.
"""

EXTRACT_SYSTEM_PROMPT = """You are a top-tier teaching assistant. Your task is to analyze the provided homework assignment and extract the top-level numbered problems to be solved.

Return one object per top-level homework problem only. Do not split subparts such as (a), (b), (c) into separate problems; keep all subparts inside the parent problem's full_text.

You must return a JSON object with a "problems" array, where each object represents a distinct top-level problem.
Example structure:
{
  "problems": [
    {
      "problem_number": "1",
      "title": "Problem 1: Rigid Bar in Electric Field",
      "full_text": "The exact full text of the problem description...",
      "images": [
        {
          "page_index": 0,
          "box_2d": [200, 150, 400, 500]
        }
      ]
    }
  ]
}
- "problem_number": the original top-level problem number as printed in the assignment, usually "1", "2", "3", etc.
- "title": a concise, safe filename-friendly title for the problem.
- "full_text": the complete text of the problem.
- "page_index": the 0-indexed page number where the relevant illustration is located.
- "box_2d": the bounding box of the illustration/image in the normalized [0, 1000] scale, in the format [ymin, xmin, ymax, xmax].
- If a problem has no illustrations, leave the "images" array empty.
- Do NOT return anything other than the JSON object.
"""

class ImageBox(BaseModel):
    page_index: int = Field(description="The 0-indexed page number where the relevant illustration is located.")
    box_2d: list[int] = Field(description="The bounding box of the illustration/image in the normalized [0, 1000] scale, in the format [ymin, xmin, ymax, xmax].")

class ProblemExtract(BaseModel):
    problem_number: str = Field(description="The original top-level problem number as printed in the assignment, usually '1', '2', '3', etc.")
    title: str = Field(description="A concise, safe filename-friendly title for the problem.")
    full_text: str = Field(description="The complete text of the problem statement.")
    images: list[ImageBox] = Field(description="List of illustrations associated with this problem. Empty if none.")

class ProblemListResponse(BaseModel):
    problems: list[ProblemExtract] = Field(description="List of all distinct problems found in the assignment.")

def yaml_escape(value: str) -> str:
    return value.replace('"', '\\"')

def submission_date() -> str:
    return datetime.now().strftime("%B %d, %Y").replace(" 0", " ")

def normalize_problem_number(problem: dict, fallback: int) -> str:
    raw_number = str(problem.get("problem_number") or "").strip()
    title = str(problem.get("title") or "").strip()

    for value in (raw_number, title):
        match = re.search(r'\b(?:problem\s*)?(\d+[A-Za-z]?)\b', value, re.IGNORECASE)
        if match:
            return match.group(1)

    return str(fallback)

def wait_for_file_active(client, uploaded_file, label: str = "file") -> str:
    """Waits until a Gemini uploaded file is ready and returns its API name."""
    while True:
        file_name = uploaded_file.name or ""
        file_info = client.files.get(name=file_name)
        state_name = file_info.state.name if file_info.state else ""
        if state_name == "ACTIVE":
            return file_name
        if state_name == "FAILED":
            raise RuntimeError(f"{label} processing failed on server.")
        print(f"[*] Waiting for {label} processing on server...")
        time.sleep(2)

def compress_solution_style(client, problem_text: str, solution_text: str) -> str:
    """Runs a lightweight pass to enforce the concise Homework 5 style."""
    if not solution_text.strip():
        return solution_text

    prompt = f"""Rewrite the solution below into concise Homework 5 style.

Problem text is provided only to preserve correctness; do not restate it.

Problem Text:
{problem_text}

Solution:
{solution_text}
"""

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt],
        config=types.GenerateContentConfig(
            system_instruction=COMPRESS_SYSTEM_PROMPT,
            temperature=0.0,
        )
    )

    compressed = response.text or ""
    return compressed.strip() or solution_text

def solve_homework(file_path: str):
    """Uses Gemini to analyze the assignment file and generate a Markdown solution.
    Returns: (full_text: str, solved_problems_data: list)
    """
    client = genai.Client(api_key=GOOGLE_API_KEY, http_options={'timeout': 300000})
    
    print(f"[*] Uploading and analyzing file: {file_path}...")
    
    full_text = ""
    solved_problems_data = []
    
    try:
        uploaded_file = client.files.upload(file=file_path)
        
        try:
            file_name = wait_for_file_active(client, uploaded_file)
        except RuntimeError as e:
            print(f"[!] Error: {e}")
            return "", []

        print(f"[*] File loaded (ID: {file_name}). Extracting problem list using flash model...")
        
        # Step 1: Extract problems
        problems = []
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                extract_response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        uploaded_file,
                        "Extract the top-level numbered homework problems. Keep all subparts inside their parent problem. Return the data adhering strictly to the schema provided."
                    ],
                    config=types.GenerateContentConfig(
                        system_instruction=EXTRACT_SYSTEM_PROMPT,
                        temperature=0.0,
                        response_mime_type="application/json",
                        response_schema=ProblemListResponse
                    )
                )
                
                # Parse the typed output
                parsed_res = extract_response.text
                if parsed_res:
                    try:
                        data = json.loads(parsed_res)
                        problems_data = data.get("problems", [])
                        for p in problems_data:
                            problems.append({
                                "problem_number": p.get("problem_number", ""),
                                "title": p.get("title", "Problem"),
                                "full_text": p.get("full_text", ""),
                                "images": p.get("images", [])
                            })
                    except Exception as parse_e:
                        print(f"Failed to parse JSON: {parse_e}")
                
                if not problems:
                    raise ValueError("Extracted problem list is empty.")
                    
                break # Success, exit retry loop
                
            except Exception as e:
                print(f"[!] Error parsing problem list (Attempt {attempt+1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    print("[*] Falling back to single-pass generation (might timeout)...")
                    problems = [{"title": "All problems", "full_text": "Please solve this entire assignment.", "images": []}]
                else:
                    print("[*] Retrying extraction...")
                    time.sleep(3)
            
        print(f"[+] Found {len(problems)} problem(s). Solving sequentially...")

        submission_title = Path(file_path).stem.replace("_", " ").replace("-", " ")
        
        full_text = f"""---
title: "{yaml_escape(submission_title)}"
author: "Cai Dongting, id: 20243941005"
date: "{submission_date()}"
---

"""
        
        # Step 2: Solve each problem sequentially
        doc = None
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            print(f"[!] Warning: Could not open file with PyMuPDF for image extraction: {e}")
            
        img_dir = os.path.join(CACHE_DIR, "hw_images")
        os.makedirs(img_dir, exist_ok=True)
        
        for i, problem in enumerate(problems, 1):
            if isinstance(problem, str):
                # Fallback for old prompt format or unexpected string output
                # Truncate to avoid OS file name limits later
                problem_str = str(problem)
                safe_title = problem_str[:50] + "..." if len(problem_str) > 50 else problem_str
                problem = {"problem_number": str(i), "title": f"Problem {i}: {safe_title}", "full_text": problem_str, "images": []}
                 
            title = problem.get("title", f"Problem {i}")
            full_text_prop = problem.get("full_text", "")
            problem_number = normalize_problem_number(problem, i)
            is_all_problems_fallback = len(problems) == 1 and title == "All problems"
             
            print(f"\n[*] Solving Problem {problem_number} ({i}/{len(problems)}): {title}", flush=True)
             
            # Format the problem text and image for the final markdown
            problem_md = "" if is_all_problems_fallback else f"# {problem_number}\n\n"
                
            has_images = False
            image_filepaths = []
            images_info = problem.get("images", [])
            if doc and images_info:
                for img_idx, img_data in enumerate(images_info):
                    try:
                        page_index = img_data.get("page_index", 0)
                        box = img_data.get("box_2d", [])
                        if len(box) == 4 and 0 <= page_index < len(doc):
                            page = doc[page_index]
                            rect = page.rect
                            width, height = rect.width, rect.height
                            
                            ymin, xmin, ymax, xmax = box
                            x0 = xmin / 1000.0 * width
                            y0 = ymin / 1000.0 * height
                            x1 = xmax / 1000.0 * width
                            y1 = ymax / 1000.0 * height
                            
                            clip_rect = fitz.Rect(max(0, x0-10), max(0, y0-10), min(width, x1+10), min(height, y1+10))
                            pix = page.get_pixmap(clip=clip_rect, dpi=150)
                            
                            base_name = Path(file_path).stem
                            img_filename = f"{base_name}_prob{i}_img{img_idx}.png"
                            img_filepath = os.path.join(img_dir, img_filename)
                            pix.save(img_filepath)
                              
                            # Use cropped figures as model context, not as final answer content.
                            has_images = True
                            image_filepaths.append(img_filepath)
                    except Exception as e:
                        print(f"[!] Warning: Failed to extract image {img_idx} for problem {i}: {e}")
                         
            full_text += problem_md
             
            prompt = f"""Solve only the following problem.

Output format:
For a problem without subparts:
Solution:
[definitions/equations/substitutions]
[final result]

For a problem with subparts:
(a) Solution:
[definitions/equations/substitutions]
[final result]

(b) Solution:
[definitions/equations/substitutions]
[final result]

Rules:
- Do not write the top-level problem number; it is added by the script.
- Match the concise Homework 5 style.
- Keep only core formulas, substitutions, short necessary conclusions, and final answers.
- Omit tutorial prose and problem restatements.
- Use at most one short sentence per subpart if needed.

Problem Text:
{full_text_prop}
"""
                 
            visual_reference = re.search(r'\b(figure|diagram|shown|sketch|image|graph)\b', full_text_prop, re.IGNORECASE)
            use_full_pdf_context = is_all_problems_fallback
            if visual_reference and not image_filepaths:
                print(f"[*] Problem {i} appears to need visual context; using full PDF for this problem.", flush=True)
                use_full_pdf_context = True
            if is_all_problems_fallback:
                prompt = """Solve this entire assignment.

Output each top-level problem with its original problem number, then concise Homework 5 style solutions.
Keep only core formulas, substitutions, short necessary conclusions, and final answers.
Omit tutorial prose and problem restatements.
"""

            uploaded_problem_files = []
            uploaded_problem_file_names = []
            if image_filepaths and not use_full_pdf_context:
                try:
                    print(f"[*] Uploading {len(image_filepaths)} cropped image(s) for Problem {i}...", flush=True)
                    for img_filepath in image_filepaths:
                        uploaded_image = client.files.upload(file=img_filepath)
                        image_file_name = wait_for_file_active(client, uploaded_image, "cropped image")
                        uploaded_problem_files.append(uploaded_image)
                        uploaded_problem_file_names.append(image_file_name)
                except Exception as e:
                    print(f"[!] Warning: cropped image upload failed for Problem {i}: {e}")
                    print("[*] Falling back to full PDF context for this problem.")
                    use_full_pdf_context = True

            solve_contents = []
            if use_full_pdf_context:
                solve_contents.append(uploaded_file)
            else:
                solve_contents.extend(uploaded_problem_files)
            solve_contents.append(prompt)
                 
            retries = 3
            problem_text = ""
            for attempt in range(retries):
                try:
                    # Request only the single problem text plus any cropped figures.
                    response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=solve_contents,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.2,
                        )
                    )
                     
                    problem_text = response.text or ""
                    try:
                        problem_text = compress_solution_style(client, full_text_prop, problem_text)
                    except Exception as e:
                        print(f"[!] Warning: style compression failed for Problem {i}: {e}")
                     
                    full_text += f"\n\n{problem_text}\n\n"
                    print(f"\n[+] Problem {i} generated.", flush=True)
                    break # Success, break retry loop
                except Exception as e:
                    print(f"\n[!] Error solving '{problem}' (Attempt {attempt+1}/{retries}): {e}", flush=True)
                    if attempt == retries - 1:
                        print(f"[!] Failed to solve '{title}' after all retries.", flush=True)
                        error_msg = f"\n\n> **Error:** Failed to generate solution for '{title}' due to API errors.\n\n\\newpage\n\n"
                        full_text += error_msg
                        problem_text = error_msg
                    else:
                        time.sleep(5) # Wait before retry

            for image_file_name in uploaded_problem_file_names:
                try:
                    client.files.delete(name=image_file_name)
                except Exception as e:
                    print(f"[!] Warning: failed to delete cropped image {image_file_name}: {e}")
             
            solved_problems_data.append({
                "problem_number": problem_number,
                "title": title,
                "full_text": full_text_prop,
                "solution_md": problem_text,
                "has_images": has_images
            })
            
        print("\n\n[+] All generation complete.")
        
        if doc:
            doc.close()
        
        # Clean up the file from Google's servers after processing
        try:
            print("[*] Cleaning up uploaded file...")
            client.files.delete(name=file_name)
        except Exception as e:
            print(f"[!] Warning: failed to delete file {file_name}: {e}")
        
        return full_text, solved_problems_data
    except Exception as e:
        print(f"\n[!] Error during API call: {e}")
        if full_text:
            return full_text, solved_problems_data
        return "", []

def fix_markdown_math(text: str) -> str:
    """Fixes common markdown math formatting issues that break Pandoc/LaTeX."""
    # Fix instances where AI accidentally escapes dollar signs like \$
    text = text.replace(r'\$', '$')
    
    # Fix block math spacing to prevent Pandoc from ignoring them due to blank lines
    # Pandoc math blocks fail if there are blank lines immediately after $$
    text = re.sub(r'[$][$]\n+', '$$\n', text)
        # Ensure there's a double newline BEFORE $$ so standard Markdown parsers treat it as a block
    text = re.sub(r'(?<!\n)\n[$][$]', '\n\n$$', text)
    
    # Pandoc might choke on '---' if it misinterprets it as a YAML block at the very start
    # Since we use '---' as problem separators, let's just make sure the file doesn't start with it
    text = text.lstrip()
    if text.startswith('---'):
        text = '\n' + text
    
    # Clean spaces inside inline math (Pandoc requires no spaces next to the $ signs)
    # Replaces "$ math $" with "$math$"
    def clean_inline_math(match):
        content = match.group(1).strip()
        # Remove any newlines within inline math, as Pandoc strictly forbids them
        content = content.replace('\n', ' ')
        return f"${content}$"
    
    # Negative lookbehind and lookahead to avoid replacing $$ blocks
    dollar = '$'
    regex = r'(?<![{0}])[{0}]([^{0}]+?)[{0}](?![{0}])'.format(dollar)
    text = re.sub(regex, clean_inline_math, text)
    
    return text

def convert_markdown_to_tex(markdown_content: str, output_tex_path: str, output_md_path: str):
    """Converts Markdown content to a TeX file using pandoc."""
    print("[*] Converting Markdown to TeX using Pandoc...")
    
    markdown_content = fix_markdown_math(markdown_content)
    
    with open(output_md_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
        
    try:
        # Generate TeX
        tex_cmd = [
            "pandoc",
            output_md_path,
            "-o", output_tex_path,
            "-s",
            "-f", "markdown",
            "-V", "geometry:margin=1in"
        ]
        print(f"[*] Running Pandoc command for TeX: {' '.join(tex_cmd)}")
        result = subprocess.run(tex_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            print(f"[!] Pandoc TeX conversion had warnings/errors:\n{result.stderr}")
            if not os.path.exists(output_tex_path):
                print(f"[!] Failed to generate TeX. Saved raw Markdown to: {output_md_path}")
                return False
                
    except subprocess.TimeoutExpired:
        print(f"[!] Pandoc conversion timed out after 60 seconds. Saved raw Markdown to: {output_md_path}")
        return False
        
    print(f"[+] Successfully generated TeX: {output_tex_path}")
    return True

def main():
    parser = argparse.ArgumentParser(description="Automated Homework Solver & TeX Generator")
    parser.add_argument("filepath", help="Path to the input assignment file (PDF, PNG, JPG, etc.)")
    args = parser.parse_args()

    input_path = Path(args.filepath)
    if not input_path.exists():
        print(f"[!] Error: File '{input_path}' does not exist.")
        sys.exit(1)

    os.makedirs(TARGET_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    base_name = input_path.stem
    output_md_name = f"{base_name}_Submission.md"
    output_tex_name = f"{base_name}_Submission.tex"
    
    output_md_path = os.path.join(CACHE_DIR, output_md_name)
    
    # Save the final TeX next to the input file for easy access
    output_tex_path = str(input_path.parent / output_tex_name)

    print(f"[*] Starting homework processing pipeline for: {input_path.name}")
    
    markdown_result, _ = solve_homework(str(input_path))
    
    if not markdown_result:
        print("[!] Failed to generate solution from AI.")
        sys.exit(1)
        
    print("\n\n[+] AI Solution generated successfully.")
        
    if not convert_markdown_to_tex(markdown_result, output_tex_path, output_md_path):
        print("[!] Failed to generate final TeX output.")
        sys.exit(1)
    
    print("\n============================================")
    print(f"[+] 作业解答已生成:")
    print(f"    - TeX: {output_tex_path}")
    print(f"    - Markdown 缓存: {output_md_path}")
    print("============================================\n")

if __name__ == "__main__":
    main()
