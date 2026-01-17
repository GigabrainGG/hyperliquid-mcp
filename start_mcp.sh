#!/bin/bash

# Load env vars from .env into the shell session
set -o allexport
source .env 2>/dev/null
set +o allexport

# Run your MCP
export HYPERLIQUID_PRIVATE_KEY=0xe8da3ac67a25aa9cc66e34912d85b9bffc65bedc0527d4a2d017ddc80ab26f6b
export HYPERLIQUID_TESTNET=false
export HYPERLIQUID_ACCOUNT_ADDRESS=0x6dc56A37C5dB6Ea8Df482105fEE89e639b2ef55f

cd /Users/admin/Documents/work/hyperliquid-mcp
/Users/admin/.local/bin/uv run python3 main.py
