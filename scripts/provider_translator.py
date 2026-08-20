from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.antigravity_backend import extract_json_object


def _toml_value(root: Path, section: str, key: str, default: str) -> str:
    path = root / "setting.toml"
    if not path.exists():
        return default
    current = ""
    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*[\"'](.*?)[\"']\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current == section:
            match = pattern.match(line)
            if match:
                return match.group(1)
    return default


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _extract_placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{[^{}]+\}\}|\[\[[^\]]+\]\]", text)))


def _terms(root: Path, book: str) -> list[dict[str, Any]]:
    raw = _load_json(root / "data" / "books" / book / "terms.json", {})
    items = raw.get("items", raw.get("terms", raw if isinstance(raw, list) else [])) if isinstance(raw, (dict, list)) else []
    return [item for item in items if isinstance(item, dict)]


def _parse_translation(content: str) -> list[dict[str, str]]:
    payload = extract_json_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("翻译响应缺少 items 数组")
    result: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict) and str(item.get("id", "")).strip():
            result.append({"id": str(item["id"]).strip(), "text": str(item.get("text", ""))})
    return result


class ProviderTranslator:
    """Direct provider adapter; Novel Translator remains a storage/export tool."""

    def __init__(self, *, novel_root: Path, manifest: Path, timeout: int = 600) -> None:
        self.novel_root = novel_root
        self.manifest = manifest
        self.timeout = timeout

    def _provider_config(self, provider: str) -> tuple[str, str, str]:
        if provider == "gemini":
            base_url = os.environ.get("PRIMARY_BASE_URL", "http://127.0.0.1:1235/v1")
            model = os.environ.get("PRIMARY_MODEL", "gemini-3.7-flash")
            api_key = os.environ.get("PRIMARY_API_KEY", "antigravity")
        elif provider == "murasaki-local":
            base_url = os.environ.get("MURASAKI_BASE_URL", "http://127.0.0.1:1234/v1")
            model = os.environ.get("MURASAKI_MODEL", "murasaki-14b-v0.2")
            api_key = os.environ.get("MURASAKI_API_KEY", "lm-studio")
        else:
            raise ValueError(f"未知翻译 provider：{provider}")
        return base_url.rstrip("/"), model, api_key

    def _system_prompt(self) -> str:
        path = self.novel_root / "prompts" / "novel_translation_system.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "你是专业小说译者。严格忠实翻译输入内容，只输出 JSON。"

    def _payload(self, book: str, ids: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
        manifest = _load_json(self.manifest, {})
        paragraphs = [p for chapter in manifest.get("chapters", []) for p in chapter.get("paragraphs", [])]
        by_id = {str(item.get("id")): item for item in paragraphs}
        selected = [by_id[item_id] for item_id in ids if item_id in by_id]
        if len(selected) != len(ids):
            missing = sorted(set(ids) - set(by_id))
            raise ValueError(f"manifest 缺少段落：{', '.join(missing)}")
        first_index = next(index for index, item in enumerate(paragraphs) if str(item.get("id")) == ids[0])
        chapter = next((item for item in manifest.get("chapters", []) if any(str(p.get("id")) == ids[0] for p in item.get("paragraphs", []))), {})
        previous = paragraphs[max(0, first_index - 3):first_index]
        next_index = first_index + len(selected)
        following = paragraphs[next_index:next_index + 2]
        payload = {
            "source_language": "auto",
            "target_language": "zh-Hans",
            "quality_profile": {
                "style_guide": "自然流畅的简体中文小说译文，忠实原意，避免生硬直译。",
                "dialogue_style": "符合中文小说阅读习惯，称谓、语气和人物关系保持连续。",
                "self_check_passes": 2,
                "requirements": [
                    "忠实保留原文事实、动作顺序、视角、语气和信息量，不总结、不删减、不扩写剧情。",
                    "保留段落边界、数字、标点含义、换行意图、HTML 标签、脚注锚点和所有 placeholders。",
                    "同一批次和上下文中的人名、地名、组织名、技能名、称号和代词指代保持一致。",
                    "只输出最终 JSON，不要解释。",
                ],
            },
            "glossary": _terms(self.novel_root, book),
            "context": {
                "chapter_id": str(chapter.get("id", "")),
                "chapter_title": str(chapter.get("title", "")),
                "previous": [{"id": str(item.get("id")), "source": str(item.get("source", "")), "translated": str(item.get("translated", ""))} for item in previous],
                "next": [{"id": str(item.get("id")), "source": str(item.get("source", ""))} for item in following],
            },
            "items": [
                {"id": str(item["id"]), "text": str(item.get("source", "")), "placeholders": _extract_placeholders(str(item.get("source", "")))}
                for item in selected
            ],
        }
        return payload, {str(item["id"]): str(item.get("source", "")) for item in selected}

    def _request(self, provider: str, payload: dict[str, Any], max_tokens: int) -> tuple[list[dict[str, str]], dict[str, Any]]:
        base_url, model, api_key = self._provider_config(provider)
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(response.status)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            text = raw.casefold()
            reason = "content_filter" if any(marker in text for marker in ("provider_blocked", "sensitive words", "content policy", "prohibited use policy")) else "http_error"
            return [], {"status": "blocked" if reason == "content_filter" else "error", "provider": provider, "reason": reason, "http_status": exc.code, "raw_response": raw[:4000]}
        except (URLError, TimeoutError, OSError) as exc:
            return [], {"status": "error", "provider": provider, "reason": "network", "error": str(exc)}
        response = _load_json_from_text(raw)
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(response, dict) else ""
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        try:
            items = _parse_translation(str(content))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return [], {"status": "error", "provider": provider, "reason": "output_format", "error": str(exc), "raw_response": raw[:4000]}
        return items, {"status": "ok", "provider": provider, "http_status": status, "raw_response": str(content)[:1000]}

    def __call__(self, provider: str, book: str, ids: list[str], *, source_chars: int, max_tokens: int) -> dict[str, Any]:
        if not ids:
            return {"status": "ok", "provider": provider, "summary": {"translated": 0}}
        payload, sources = self._payload(book, ids)
        items, result = self._request(provider, payload, max_tokens)
        if result.get("status") != "ok":
            return result
        expected = set(ids)
        received = {item["id"] for item in items}
        missing = sorted(expected - received)
        unknown = sorted(received - expected)
        if missing or unknown:
            return {"status": "error", "provider": provider, "reason": "output_format", "missing": missing, "unknown": unknown, "raw_response": result.get("raw_response", "")}
        manifest = _load_json(self.manifest, {})
        by_id = {str(item.get("id")): item for chapter in manifest.get("chapters", []) for item in chapter.get("paragraphs", [])}
        for item in items:
            text = item["text"].strip()
            if not text:
                return {"status": "error", "provider": provider, "reason": "empty_translation", "id": item["id"]}
            by_id[item["id"]]["translated"] = text
        self._atomic_write(manifest)
        result["summary"] = {"translated": len(items), "source_chars": sum(len(sources[item_id]) for item_id in ids)}
        return result

    def _atomic_write(self, manifest: dict[str, Any]) -> None:
        temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=self.manifest.parent, delete=False)
        try:
            temporary.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            temporary.close()
            Path(temporary.name).replace(self.manifest)
        finally:
            Path(temporary.name).unlink(missing_ok=True)


def _load_json_from_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
