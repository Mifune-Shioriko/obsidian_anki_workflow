import os
import json
import time
from pathlib import Path

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vault_index.json")
CACHE_VERSION = 1
SKIP_DIRS = {'.obsidian', '.trash', '.git'}

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        if cache.get('version') != CACHE_VERSION:
            return None
        return cache
    except Exception as e:
        print(f"[vault_index] Warning: Failed to load cache: {e}")
        return None

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[vault_index] Warning: Failed to save cache: {e}")

def build_index(vault_dir):
    cache = load_cache()
    vault_path = Path(vault_dir).expanduser().resolve()
    
    if cache and cache.get('vault_root') != str(vault_path):
        cache = None
    
    old_index = cache.get('index', {}) if cache else {}
    old_meta = cache.get('file_meta', {}) if cache else {}
    
    new_index = {}
    new_meta = {}
    cache_updated = False
    
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        
        for file in files:
            file_path = Path(root) / file
            file_key = file.lower()
            full_path_str = str(file_path)
            
            new_index[file_key] = full_path_str
            
            try:
                stat = file_path.stat()
                new_meta[full_path_str] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size
                }
            except Exception:
                pass
    
    if new_index != old_index or new_meta != old_meta:
        cache_updated = True
    
    if cache_updated:
        new_cache = {
            'version': CACHE_VERSION,
            'vault_root': str(vault_path),
            'last_scan': int(time.time()),
            'index': new_index,
            'file_meta': new_meta
        }
        save_cache(new_cache)
        print(f"[vault_index] Index updated: {len(new_index)} files cached")
    
    return new_index

def get_vault_index(vault_dir):
    if not vault_dir:
        return {}
    return build_index(vault_dir)
