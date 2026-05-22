#!/usr/bin/env zsh

# 1. 检查用户级 Qdrant 服务是否处于 active 状态
if ! systemctl --user is-active --quiet qdrant; then
    echo "[*] 检测到 Qdrant 服务未启动，正在启动服务..."
    systemctl --user start qdrant
    # 等待 2 秒确保其服务接口 6333 成功监听
    sleep 2
fi

# 2. 激活虚拟环境并执行相似度更新脚本
if [ -f "/home/shioriko/scripts/Obsidian/venv/bin/activate" ]; then
    source "/home/shioriko/scripts/Obsidian/venv/bin/activate"
else
    # 兜底：若不在 venv，尝试当前 Python 环境
    echo "[*] 未找到虚拟环境，使用系统默认 python..."
fi

python "/home/shioriko/scripts/Obsidian/Anki/similarity_manager.py"
