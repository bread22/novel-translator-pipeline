from __future__ import annotations

import asyncio
import os

import httpx
from fastapi import Request

from translator.web.app import create_app


def test_protected_api_accepts_browser_query_token_and_cookie() -> None:
    async def exercise() -> None:
        original = os.environ.get("WEB_AUTH_TOKEN")
        os.environ["WEB_AUTH_TOKEN"] = "fixture-token"
        try:
            app = create_app()

            async def auth_probe(_request: Request) -> dict[str, bool]:
                return {"ok": True}

            app.add_api_route("/api/v1/auth-probe", auth_probe, methods=["GET"])
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                assert (await client.get("/api/v1/auth-probe")).status_code == 401
                # A protected but unknown API path reaches routing instead of being rejected by auth.
                assert (await client.get("/api/v1/auth-probe", params={"access_token": "fixture-token"})).status_code == 404
                client.cookies.set("web_auth_token", "fixture-token")
                assert (await client.get("/api/v1/auth-probe")).status_code == 404
        finally:
            if original is None:
                os.environ.pop("WEB_AUTH_TOKEN", None)
            else:
                os.environ["WEB_AUTH_TOKEN"] = original

    asyncio.run(exercise())
