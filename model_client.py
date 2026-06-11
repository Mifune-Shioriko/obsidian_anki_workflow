import os
import inspect
import json
import base64
import re
import requests
import fitz  # PyMuPDF
import io
from PIL import Image
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load dotenv to ensure self-contained env loading
current_dir = Path(__file__).resolve().parent
env_path = current_dir / '.env'
if not env_path.exists():
    env_path = current_dir.parent / '.env'
load_dotenv(dotenv_path=env_path)

# ================= 1. Helper Functions =================

def compress_image_bytes(img_bytes, max_size=1024, quality=75):
    try:
        img = Image.open(io.BytesIO(img_bytes))
        # Convert RGBA to RGB to avoid issues when saving as JPEG
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
            
        # Resize if dimension exceeds max_size
        width, height = img.size
        if max(width, height) > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
        # Compress and save to JPEG bytes
        out_io = io.BytesIO()
        img.save(out_io, format="JPEG", quality=quality)
        return out_io.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[model_client] Warning: Failed to compress image: {e}. Using original bytes.")
        return img_bytes, None

def extract_pdf_text_from_path(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text_list = []
        for page in doc:
            text_list.append(page.get_text())
        return "\n\n--- PAGE BREAK ---\n\n".join(text_list)
    except Exception as e:
        print(f"[model_client] Error: Failed to extract PDF text from {pdf_path}: {e}")
        return f"[Failed to extract text from PDF: {pdf_path}]"

def function_to_schema(func):
    import inspect
    sig = inspect.signature(func)
    doc = func.__doc__ or ""
    
    # Simple docstring description
    description = doc.strip().split("\n")[0]
    
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }
    
    for name, param in sig.parameters.items():
        p_type = "string"
        if param.annotation == int:
            p_type = "integer"
        elif param.annotation == bool:
            p_type = "boolean"
        elif param.annotation == float:
            p_type = "number"
            
        parameters["properties"][name] = {
            "type": p_type,
            "description": ""
        }
        if param.default == inspect.Parameter.empty:
            parameters["required"].append(name)
            
    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": description,
            "parameters": parameters
        }
    }

def convert_parts_to_openai_content(parts):
    content_blocks = []
    
    for part in parts:
        if isinstance(part, str):
            content_blocks.append({"type": "text", "text": part})
        elif isinstance(part, dict):
            if "text" in part:
                content_blocks.append({"type": "text", "text": part["text"]})
        # Check standard properties of google.genai.types.Part
        elif hasattr(part, 'text') and part.text:
            content_blocks.append({"type": "text", "text": part.text})
        elif hasattr(part, 'inline_data') and part.inline_data:
            mime_type = part.inline_data.mime_type
            data_bytes = part.inline_data.data
            compressed_bytes, new_mime = compress_image_bytes(data_bytes)
            mime_type = new_mime or mime_type
            base64_str = base64.b64encode(compressed_bytes).decode('utf-8')
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64_str}"
                }
            })
        elif hasattr(part, 'file_data') and part.file_data:
            file_uri = part.file_data.file_uri
            mime_type = part.file_data.mime_type
            file_path = file_uri.replace("file://", "")
            
            if "pdf" in mime_type.lower() or file_path.lower().endswith(".pdf"):
                extracted_text = extract_pdf_text_from_path(file_path)
                content_blocks.append({"type": "text", "text": extracted_text})
            elif "image" in mime_type.lower() or any(file_path.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                with open(file_path, 'rb') as f:
                    img_bytes = f.read()
                compressed_bytes, new_mime = compress_image_bytes(img_bytes)
                mime_type = new_mime or mime_type
                base64_str = base64.b64encode(compressed_bytes).decode('utf-8')
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_str}"
                    }
                })
        elif isinstance(part, MockFile):
            file_path = part.file_path
            mime_type = part.mime_type
            if "pdf" in mime_type.lower() or file_path.lower().endswith(".pdf"):
                extracted_text = extract_pdf_text_from_path(file_path)
                content_blocks.append({"type": "text", "text": extracted_text})
            elif any(file_path.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                with open(file_path, 'rb') as f:
                    img_bytes = f.read()
                compressed_bytes, new_mime = compress_image_bytes(img_bytes)
                mime_type = new_mime or mime_type
                base64_str = base64.b64encode(compressed_bytes).decode('utf-8')
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_str}"
                    }
                })
                
    if len(content_blocks) == 1 and content_blocks[0]["type"] == "text":
        return content_blocks[0]["text"]
    return content_blocks

def convert_contents_to_messages(contents):
    messages = []
    
    if not isinstance(contents, list):
        contents_list = [contents]
    else:
        contents_list = contents
        
    for item in contents_list:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
        elif isinstance(item, dict):
            role = item.get("role", "user")
            if role in ["model", "assistant"]:
                role = "assistant"
            else:
                role = "user"
            parts = item.get("parts", [])
            content = convert_parts_to_openai_content(parts)
            messages.append({"role": role, "content": content})
        elif hasattr(item, 'role') and hasattr(item, 'parts'):
            role = item.role
            if role in ["model", "assistant"]:
                role = "assistant"
            else:
                role = "user"
            content = convert_parts_to_openai_content(item.parts)
            messages.append({"role": role, "content": content})
        elif isinstance(item, MockFile):
            file_path = item.file_path
            mime_type = item.mime_type
            if "pdf" in mime_type.lower() or file_path.lower().endswith(".pdf"):
                extracted_text = extract_pdf_text_from_path(file_path)
                messages.append({"role": "user", "content": extracted_text})
            elif any(file_path.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                with open(file_path, 'rb') as f:
                    img_bytes = f.read()
                compressed_bytes, new_mime = compress_image_bytes(img_bytes)
                mime_type = new_mime or mime_type
                base64_str = base64.b64encode(compressed_bytes).decode('utf-8')
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_str}"
                            }
                        }
                    ]
                })
        elif hasattr(item, 'text'):
            messages.append({"role": "user", "content": item.text})
            
    return messages

# ================= 2. Dynamic Router =================

def get_provider():
    # Load env-defined granularity
    stack = inspect.stack()
    for frame in stack:
        filename = frame.filename
        basename = os.path.basename(filename)
        
        # Check agents
        if basename in ["default.py", "explain.py", "new.py", "add.py", "dig.py", "revise.py"]:
            agent_key = f"AGENT_{basename[:-3].upper()}_PROVIDER"
            provider_env = os.getenv(agent_key)
            if provider_env:
                return provider_env.lower()
            return os.getenv("DEFAULT_LLM_PROVIDER", "qwen").lower()
            
        elif basename == "daily_to_atomic.py":
            provider_env = os.getenv("SCRIPT_DAILY_TO_ATOMIC_PROVIDER")
            if provider_env:
                return provider_env.lower()
            return os.getenv("DEFAULT_LLM_PROVIDER", "qwen").lower()
            
        elif basename == "auto_slide_processor.py":
            provider_env = os.getenv("SCRIPT_AUTO_SLIDE_PROVIDER")
            if provider_env:
                return provider_env.lower()
            return os.getenv("DEFAULT_LLM_PROVIDER", "qwen").lower()
            
        elif basename in ["file.py", "pubmed.py"]:
            agent_key = f"AGENT_{basename[:-3].upper()}_PROVIDER"
            provider_env = os.getenv(agent_key)
            if provider_env:
                return provider_env.lower()
            return "gemini"
            
        elif basename == "solve_hw.py":
            provider_env = os.getenv("SCRIPT_SOLVE_HW_PROVIDER")
            if provider_env:
                return provider_env.lower()
            return "gemini"

    return os.getenv("DEFAULT_LLM_PROVIDER", "gemini").lower()

# ================= 3. Mock SDK for Qwen =================

class MockFileState:
    def __init__(self, name="ACTIVE"):
        self.name = name

class MockFile:
    def __init__(self, file_path, name=None, mime_type=None):
        self.file_path = file_path
        self.name = name or file_path
        self.uri = f"file://{file_path}"
        self.mime_type = mime_type or self._detect_mime_type(file_path)
        self.state = MockFileState()
        
    def _detect_mime_type(self, path):
        low = path.lower()
        if low.endswith(".pdf"): return "application/pdf"
        if low.endswith(".png"): return "image/png"
        if low.endswith(".jpg") or low.endswith(".jpeg"): return "image/jpeg"
        if low.endswith(".webp"): return "image/webp"
        if low.endswith(".gif"): return "image/gif"
        return "application/octet-stream"
        
    @property
    def original_name(self):
        return os.path.basename(self.file_path)

class QwenFilesAPI:
    def __init__(self):
        self._cache = {}

    def upload(self, file):
        file_path = str(file)
        mock_file = MockFile(file_path)
        self._cache[mock_file.name] = mock_file
        return mock_file

    def get(self, name):
        if name in self._cache:
            return self._cache[name]
        return MockFile(name)

    def delete(self, name):
        if name in self._cache:
            del self._cache[name]

class QwenChatSession:
    def __init__(self, client_inst, model, history, config):
        self.client_inst = client_inst
        self.model = model
        self.history = history or []
        self.config = config

    def send_message(self, message):
        user_part = types.Part.from_text(text=message)
        user_content = types.Content(role="user", parts=[user_part])
        self.history.append(user_content)
        
        res = self.client_inst.models.generate_content(
            model=self.model,
            contents=self.history,
            config=self.config
        )
        
        model_part = types.Part.from_text(text=res.text)
        model_content = types.Content(role="model", parts=[model_part])
        self.history.append(model_content)
        
        return res

class QwenChatsAPI:
    def __init__(self, client_inst):
        self.client_inst = client_inst

    def create(self, model, history, config):
        return QwenChatSession(self.client_inst, model, history, config)

class QwenResponse:
    def __init__(self, text, parsed=None):
        self.text = text
        self.parsed = parsed

class QwenModelsAPI:
    def generate_content(self, model, contents, config=None):
        api_key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("[model_client] QWEN_API_KEY not found in environment variables.")
            
        qwen_model = os.getenv("QWEN_MODEL_NAME", "qwen3.7-plus")
        
        messages = []
        system_instruction = None
        temperature = 0.7
        response_mime_type = None
        response_schema = None
        enable_search = False
        tools = None
        
        if config:
            if hasattr(config, 'system_instruction') and config.system_instruction:
                system_instruction = config.system_instruction
            if hasattr(config, 'temperature') and config.temperature is not None:
                temperature = config.temperature
            if hasattr(config, 'response_mime_type') and config.response_mime_type:
                response_mime_type = config.response_mime_type
            if hasattr(config, 'response_schema') and config.response_schema:
                response_schema = config.response_schema
            if hasattr(config, 'tools') and config.tools:
                tools = config.tools
                for t in tools:
                    if hasattr(t, 'google_search') and t.google_search:
                        enable_search = True

        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
            
        converted_messages = convert_contents_to_messages(contents)
        messages.extend(converted_messages)
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": qwen_model,
            "messages": messages,
            "temperature": temperature
        }
        
        if enable_search:
            payload["enable_search"] = True
            
        openai_tools = []
        if tools:
            for t in tools:
                if callable(t):
                    openai_tools.append(function_to_schema(t))
            if openai_tools:
                payload["tools"] = openai_tools

        if response_schema:
            schema_dict = response_schema.model_json_schema()
            schema_str = json.dumps(schema_dict, ensure_ascii=False)
            instruction = f"\n\nYou must return a JSON object adhering strictly to this JSON Schema:\n{schema_str}"
            
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] += instruction
            else:
                messages.insert(0, {"role": "system", "content": instruction})
                
            payload["response_format"] = {"type": "json_object"}
        elif response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}

        api_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        
        while True:
            res = requests.post(api_url, json=payload, headers=headers)
            res.raise_for_status()
            res_data = res.json()
            
            message = res_data["choices"][0]["message"]
            
            if "tool_calls" in message and message["tool_calls"]:
                tool_calls = message["tool_calls"]
                messages.append(message)
                
                for tc in tool_calls:
                    func_name = tc["function"]["name"]
                    func_args_str = tc["function"]["arguments"]
                    func_args = json.loads(func_args_str)
                    
                    matched_func = None
                    if tools:
                        for tool_func in tools:
                            if callable(tool_func) and tool_func.__name__ == func_name:
                                matched_func = tool_func
                                break
                                
                    if matched_func:
                        print(f"🔧 [model_client] Executing tool: {func_name} with args {func_args}")
                        try:
                            result = matched_func(**func_args)
                        except Exception as tool_err:
                            result = f"Error executing tool: {tool_err}"
                    else:
                        result = f"Error: Tool {func_name} not found"
                        
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": func_name,
                        "content": str(result)
                    })
                    
                payload["messages"] = messages
                continue
            else:
                reply_text = message.get("content") or ""
                break
                
        if (response_schema or response_mime_type == "application/json") and reply_text.strip().startswith("```"):
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", reply_text)
            if match:
                reply_text = match.group(1)
                
        parsed_obj = None
        if response_schema and reply_text.strip():
            try:
                parsed_obj = response_schema.model_validate_json(reply_text)
            except Exception as e:
                print(f"[model_client] Pydantic parsing failed: {e}. Raw response: {reply_text}")
                try:
                    data = json.loads(reply_text)
                    parsed_obj = response_schema(**data)
                except Exception as e2:
                    print(f"[model_client] Fallback dict parsing failed: {e2}")
                    raise e
                    
        return QwenResponse(text=reply_text, parsed=parsed_obj)

# ================= 4. Unified Gateway Client =================

class Client:
    def __init__(self, api_key=None, http_options=None):
        self.provider = get_provider()
        
        if self.provider == "gemini":
            print(f"[model_client] Dynamically routing call to Google Gemini...")
            self._real_client = genai.Client(api_key=api_key, http_options=http_options)
            self.models = self._real_client.models
            self.files = self._real_client.files
            self.chats = self._real_client.chats
        else:
            print(f"[model_client] Dynamically routing call to Tongyi Qianwen (Qwen)...")
            self._real_client = None
            self.models = QwenModelsAPI()
            self.files = QwenFilesAPI()
            self.chats = QwenChatsAPI(self)
