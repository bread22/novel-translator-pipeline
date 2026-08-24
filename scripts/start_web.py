#!/usr/bin/env python3
"""Entry point for running Novel Translator Studio Web Server."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure root is in sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translator.web import run_server


def main() -> int:
    parser = argparse.ArgumentParser(description="Novel Translator Studio Web Server")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)), help="Port to bind (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument("--auth-token-env", help="Read the bearer token from this environment variable")
    args = parser.parse_args()

    if args.auth_token_env:
        token = os.environ.get(args.auth_token_env, "")
        if not token:
            parser.error(f"环境变量 {args.auth_token_env} 未设置")
        os.environ["WEB_AUTH_TOKEN"] = token

    print(f"\n=======================================================")
    print(f" 🚀 Novel Translator Studio is starting...")
    print(f" 🌐 Local URL:    http://127.0.0.1:{args.port}")
    print(f" 📖 API Docs:     http://127.0.0.1:{args.port}/docs")
    if args.host in {"0.0.0.0", "::"}:
        print(" ⚠️  服务将监听所有网络接口；建议同时配置 --auth-token-env。")
    print(f"=======================================================\n")

    run_server(host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
