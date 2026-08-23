from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import time
from typing import Any

from fastapi import APIRouter, HTTPException
import tomli_w

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

from translator.core.config import (
    _load_dotenv,
    dual_review_enabled,
    fallback_translators_names,
    load_config,
    primary_translator_name,
    read_env_keys,
    reviewer_name,
    write_env_key,
)
from translator.providers.registry import create_provider
from translator.web.models import PreflightProviderResult, PreflightResponse


ROOT = Path(__file__).resolve().parents[3]
router = APIRouter(prefix="/system", tags=["System"])


def get_config_path() -> Path:
    return ROOT / "config.toml"


def _env_var_name_for_provider(p_name: str) -> str:
    p_lower = p_name.lower()
    if p_lower == "nemotron" or "nvidia" in p_lower:
        return "NVIDIA_API_KEY"
    if p_lower == "deepseek":
        return "DEEPSEEK_API_KEY"
    if "gemini" in p_lower:
        return "GEMINI_API_KEY"
    if "openai" in p_lower:
        return "OPENAI_API_KEY"
    clean = p_name.upper().replace("-", "_").replace(".", "_")
    return f"{clean}_API_KEY"


@router.get("/config")
def get_system_config() -> dict[str, Any]:
    _load_dotenv(override=True)
    cfg = load_config()
    # Resolve actual API keys for UI display
    providers = cfg.get("providers", {})
    for p_name, p_conf in providers.items():
        raw_key = str(p_conf.get("api_key", "")).strip()
        if raw_key.startswith("$"):
            env_var = raw_key[1:]
            val = os.environ.get(env_var, "")
            p_conf["api_key"] = val
    return cfg


@router.post("/config")
def save_system_config(config_data: dict[str, Any]) -> dict[str, Any]:
    config_file = get_config_path()
    try:
        # Separate API keys into .env and keep references in config.toml
        providers = config_data.get("providers", {})
        for p_name, p_conf in providers.items():
            if not isinstance(p_conf, dict):
                continue
            key_val = str(p_conf.get("api_key", "")).strip()
            if not key_val or key_val in {"sk-...", "lm-studio"}:
                continue
            if key_val.startswith("$"):
                # Reference existing env var
                continue
            # Real key entered -> Save to .env securely!
            env_var = _env_var_name_for_provider(p_name)
            write_env_key(env_var, key_val)
            p_conf["api_key"] = f"${env_var}"

        raw_toml = tomli_w.dumps(config_data)
        config_file.write_text(raw_toml, encoding="utf-8")
        _load_dotenv(override=True)
        validated = load_config(config_file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"保存配置文件失败: {e}")
    return {"status": "ok", "config": validated}


@router.get("/env")
def get_env_variables() -> dict[str, str]:
    _load_dotenv(override=True)
    return read_env_keys()


@router.post("/env")
def set_env_variables(env_data: dict[str, str]) -> dict[str, Any]:
    for k, v in env_data.items():
        if k and isinstance(k, str):
            write_env_key(k.strip(), str(v).strip())
    _load_dotenv(override=True)
    return {"status": "ok", "env": read_env_keys()}


def get_prompts_dir() -> Path:
    p = (ROOT / "docs" / "prompts").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/prompts")
def list_prompts() -> list[dict[str, Any]]:
    prompts_dir = get_prompts_dir()
    prompts: list[dict[str, Any]] = []

    friendly_names = {
        "erotic-novel-policy.md": "限制级/轻小说文学规范 (Erotic Policy)",
        "general-novel-policy.md": "通用全年龄小说文学规范 (General Policy)",
        "light-novel-policy.md": "日式轻小说与二次元风格规范 (Light Novel Policy)",
        "translation-policy.md": "标准文学严谨翻译规范 (Standard Policy)",
        "consistency-review-policy.md": "长程一致性与客观缺陷审阅规范 (Consistency Review Policy)",
    }

    for file_path in sorted(prompts_dir.glob("*.md")):
        content = file_path.read_text(encoding="utf-8")
        filename = file_path.name
        is_review = "review" in filename or "审阅" in content[:100]
        first_heading = ""
        for line in content.splitlines():
            if line.strip().startswith("#"):
                first_heading = line.strip().lstrip("#").strip()
                break
        name = friendly_names.get(filename, first_heading or filename)
        prompts.append({
            "id": filename,
            "filename": filename,
            "path": f"docs/prompts/{filename}",
            "name": name,
            "type": "review" if is_review else "translation",
            "content": content,
        })
    return prompts


@router.get("/prompts/{prompt_id}")
def get_prompt_detail(prompt_id: str) -> dict[str, Any]:
    prompts_dir = get_prompts_dir()
    file_path = prompts_dir / prompt_id
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"未找到提示词文件: {prompt_id}")
    content = file_path.read_text(encoding="utf-8")
    is_review = "review" in prompt_id
    return {
        "id": prompt_id,
        "filename": prompt_id,
        "path": f"docs/prompts/{prompt_id}",
        "type": "review" if is_review else "translation",
        "content": content,
    }


@router.post("/prompts")
def save_prompt(prompt_data: dict[str, Any]) -> dict[str, Any]:
    filename = str(prompt_data.get("filename", "")).strip()
    content = str(prompt_data.get("content", "")).strip()
    if not filename.endswith(".md"):
        filename = f"{filename}.md"
    filename = filename.lower().replace(" ", "-")

    if not content:
        raise HTTPException(status_code=400, detail="Prompt 内容不能为空")

    prompts_dir = get_prompts_dir()
    target_file = prompts_dir / filename
    target_file.write_text(content, encoding="utf-8")

    return {
        "status": "ok",
        "id": filename,
        "path": f"docs/prompts/{filename}",
        "message": f"Prompt '{filename}' 已保存",
    }


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str) -> dict[str, Any]:
    protected = {"erotic-novel-policy.md", "general-novel-policy.md", "translation-policy.md"}
    if prompt_id in protected:
        raise HTTPException(status_code=400, detail="默认系统 Prompt 规范不可删除")

    prompts_dir = get_prompts_dir()
    target_file = prompts_dir / prompt_id
    if not target_file.exists():
        raise HTTPException(status_code=404, detail=f"未找到文件: {prompt_id}")

    target_file.unlink(missing_ok=True)
    return {"status": "ok", "message": f"Prompt '{prompt_id}' 已删除"}


@router.post("/preflight", response_model=PreflightResponse)
def run_system_preflight() -> PreflightResponse:
    _load_dotenv(override=True)
    config = load_config()
    providers_config = config.get("providers", {})
    roles = config.get("roles", {})

    primary = primary_translator_name(config)
    fallbacks = fallback_translators_names(config)
    reviewer = reviewer_name(config)
    secondary_reviewer = roles.get("secondary_reviewer")
    is_dual_review = dual_review_enabled(config)

    role_mapping: dict[str, list[str]] = {}
    if primary:
        role_mapping.setdefault(primary, []).append("主译 (Primary)")
    for idx, fb in enumerate(fallbacks, start=1):
        role_mapping.setdefault(fb, []).append(f"备用 #{idx} (Fallback)")
    if reviewer:
        role_mapping.setdefault(reviewer, []).append("主审 (Reviewer)")
    if secondary_reviewer and is_dual_review:
        role_mapping.setdefault(secondary_reviewer, []).append("副审 (Secondary Reviewer)")

    def probe_single_provider(p_name: str, p_conf: dict[str, Any]) -> PreflightProviderResult:
        p_type = p_conf.get("type", "unknown")
        is_assigned = p_name in role_mapping
        role_desc = " / ".join(role_mapping.get(p_name, ["未分配"]))
        model_name = p_conf.get("model", "")
        t0 = time.time()
        timeout = 12 if is_assigned else 3
        try:
            provider_inst = create_provider(p_name, config)
            check_res = provider_inst.health_check(timeout=timeout)
            latency = round((time.time() - t0) * 1000, 1)

            is_ok = False
            err_detail = ""
            if isinstance(check_res, dict):
                is_ok = check_res.get("status") == "ok"
                err_detail = str(check_res.get("error", ""))
                model_name = str(check_res.get("model", model_name))
            elif isinstance(check_res, bool):
                is_ok = check_res

            if is_ok:
                return PreflightProviderResult(
                    provider=p_name,
                    type=p_type,
                    role=role_desc,
                    status="ok",
                    latency_ms=latency,
                    model=model_name,
                    message="连通性正常，模型响应就绪",
                )
            else:
                return PreflightProviderResult(
                    provider=p_name,
                    type=p_type,
                    role=role_desc,
                    status="failed",
                    latency_ms=latency,
                    model=model_name,
                    message=f"预检未通过: {err_detail[:180]}" if err_detail else "健康探测未返回预期响应",
                )
        except Exception as exc:
            latency = round((time.time() - t0) * 1000, 1)
            return PreflightProviderResult(
                provider=p_name,
                type=p_type,
                role=role_desc,
                status="failed",
                latency_ms=latency,
                model=model_name,
                message=f"探测异常: {str(exc)[:180]}",
            )

    results: list[PreflightProviderResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(providers_config) or 1)) as executor:
        futures = [
            executor.submit(probe_single_provider, p_name, p_conf)
            for p_name, p_conf in providers_config.items()
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Sort results: assigned roles first, then alphabetically
    results.sort(key=lambda r: (0 if r.role != "未分配" else 1, r.provider))

    # All active roles passed
    active_passed = all(r.status == "ok" for r in results if r.role != "未分配")

    return PreflightResponse(all_passed=active_passed, results=results)
