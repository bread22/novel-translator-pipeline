from __future__ import annotations

import concurrent.futures
import copy
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from fastapi import APIRouter, HTTPException
import tomli_w

from translator.core.config import (
    _load_dotenv,
    create_config_backup,
    dual_review_enabled,
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
    validate_config_data,
    write_env_keys,
)
from translator.providers.registry import create_provider
from translator.core.paths import PathResolver
from translator.review.knowledge_extractor import (
    knowledge_extractor_connection_test,
    knowledge_extractor_enabled,
    knowledge_extractor_provider_names,
)
from translator.web.models import PreflightProviderResult, PreflightResponse
from translator.web.path_policy import resolve_under, validate_prompt_filename


ROOT = Path(__file__).resolve().parents[3]
router = APIRouter(prefix="/system", tags=["System"])
FIXED_KNOWLEDGE_PROMPTS = {
    "knowledge_extractor_window.md",
    "knowledge_extractor_finalize.md",
}


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
    providers = cfg.get("providers", {})
    for p_conf in providers.values():
        raw_key = str(p_conf.get("api_key", "")).strip()
        env_var = raw_key[1:] if raw_key.startswith("$") else ""
        resolved = os.environ.get(env_var, "") if env_var else raw_key
        p_conf["api_key_ref"] = raw_key if raw_key.startswith("$") else None
        p_conf["api_key_configured"] = bool(resolved)
        p_conf["api_key_preview"] = f"••••{resolved[-4:]}" if len(resolved) >= 4 else ("••••" if resolved else None)
        p_conf.pop("api_key", None)
    return cfg


@router.post("/config")
def save_system_config(config_data: dict[str, Any]) -> dict[str, Any]:
    config_file = get_config_path()
    env_file = config_file.parent / ".env"
    original_config = config_file.read_bytes() if config_file.exists() else None
    replaced = False
    temporary_path: Path | None = None
    try:
        candidate = copy.deepcopy(config_data)
        current = load_config(config_file) if config_file.exists() else {"providers": {}}
        secret_updates: dict[str, str] = {}
        providers = candidate.get("providers", {})
        for p_name, p_conf in providers.items():
            if not isinstance(p_conf, dict):
                continue
            for read_only in ("api_key_ref", "api_key_configured", "api_key_preview"):
                p_conf.pop(read_only, None)
            key_val = str(p_conf.get("api_key", "")).strip()
            old_provider = current.get("providers", {}).get(p_name, {})
            if not key_val:
                old_key = str(old_provider.get("api_key", "")).strip()
                if old_key:
                    p_conf["api_key"] = old_key
            elif not key_val.startswith("$") and key_val not in {"sk-...", "lm-studio"}:
                env_var = _env_var_name_for_provider(p_name)
                secret_updates[env_var] = key_val
                p_conf["api_key"] = f"${env_var}"

        validate_config_data(candidate)
        base = config_file.parent.resolve()
        output_path = (base / str(candidate["paths"]["output_root"])).resolve()
        if not output_path.parent.exists() or not os.access(output_path.parent, os.W_OK):
            raise ValueError(f"输出目录的父目录不可写：{output_path.parent}")
        policy_path = resolve_under(base, str(candidate["paths"]["translation_policy"]))
        if not policy_path.is_file():
            raise ValueError(f"翻译规范不存在：{policy_path}")

        raw_toml = tomli_w.dumps(candidate)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=config_file.parent, prefix=".config.", suffix=".toml", delete=False) as stream:
            stream.write(raw_toml)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        validated = load_config(temporary_path)
        backup_path = create_config_backup(config_file) if config_file.exists() else None
        os.replace(temporary_path, config_file)
        temporary_path = None
        replaced = True
        if secret_updates:
            write_env_keys(secret_updates, env_file)
        _load_dotenv(env_file, override=True)
    except Exception as e:
        if replaced:
            if original_config is None:
                config_file.unlink(missing_ok=True)
            else:
                rollback = config_file.with_name(f".{config_file.name}.rollback")
                rollback.write_bytes(original_config)
                os.replace(rollback, config_file)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"保存配置文件失败: {e}")
    return {"status": "ok", "config": validated, "backup": str(backup_path) if backup_path else None}


@router.post("/knowledge-extractor/test")
def test_knowledge_extractor_connection() -> dict[str, Any]:
    """Probe the configured extractor provider without creating review artifacts."""
    _load_dotenv(override=True)
    return knowledge_extractor_connection_test(load_config())


def get_prompts_dir() -> Path:
    p = PathResolver.for_config(get_config_path()).prompts_root
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/prompts")
def list_prompts() -> list[dict[str, Any]]:
    prompts_dir = get_prompts_dir()
    prompts: list[dict[str, Any]] = []

    friendly_names = {
        "france-shoin-90s-classic.md": "90年代法国书院文库典藏规范 (France Shoin 1990s Classic)",
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
        prompt_type = "knowledge" if filename in FIXED_KNOWLEDGE_PROMPTS else ("review" if is_review else "translation")
        prompts.append({
            "id": filename,
            "filename": filename,
            "path": f"docs/prompts/{filename}",
            "name": name,
            "type": prompt_type,
            "editable": prompt_type != "knowledge",
            "content": content,
        })
    return prompts


@router.get("/prompts/{prompt_id}")
def get_prompt_detail(prompt_id: str) -> dict[str, Any]:
    prompts_dir = get_prompts_dir()
    try:
        file_path = resolve_under(prompts_dir, validate_prompt_filename(prompt_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"未找到提示词文件: {prompt_id}")
    content = file_path.read_text(encoding="utf-8")
    is_review = "review" in prompt_id
    friendly_names = {
        "france-shoin-90s-classic.md": "90年代法国书院文库典藏规范 (France Shoin 1990s Classic)",
        "erotic-novel-policy.md": "限制级/轻小说文学规范 (Erotic Policy)",
        "general-novel-policy.md": "通用全年龄小说文学规范 (General Policy)",
        "light-novel-policy.md": "日式轻小说与二次元风格规范 (Light Novel Policy)",
        "translation-policy.md": "标准文学严谨翻译规范 (Standard Policy)",
        "consistency-review-policy.md": "长程一致性与客观缺陷审阅规范 (Consistency Review Policy)",
    }
    first_heading = ""
    for line in content.splitlines():
        if line.strip().startswith("#"):
            first_heading = line.strip().lstrip("#").strip()
            break
    name = friendly_names.get(prompt_id, first_heading or prompt_id)
    prompt_type = "knowledge" if prompt_id in FIXED_KNOWLEDGE_PROMPTS else ("review" if is_review else "translation")
    return {
        "id": prompt_id,
        "filename": prompt_id,
        "path": f"docs/prompts/{prompt_id}",
        "name": name,
        "type": prompt_type,
        "editable": prompt_type != "knowledge",
        "content": content,
    }


@router.post("/prompts")
def save_prompt(prompt_data: dict[str, Any]) -> dict[str, Any]:
    filename = str(prompt_data.get("filename", "")).strip()
    content = str(prompt_data.get("content", "")).strip()
    if not filename.endswith(".md"):
        filename = f"{filename}.md"
    filename = filename.lower().replace(" ", "-")

    if filename in FIXED_KNOWLEDGE_PROMPTS:
        raise HTTPException(status_code=400, detail="Knowledge Extractor 固定提示词不可编辑")

    if not content:
        raise HTTPException(status_code=400, detail="Prompt 内容不能为空")

    prompts_dir = get_prompts_dir()
    try:
        target_file = resolve_under(prompts_dir, validate_prompt_filename(filename))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=prompts_dir,
            prefix=f".{filename}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary_path = Path(stream.name)
        os.replace(temporary_path, target_file)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return {
        "status": "ok",
        "id": filename,
        "path": f"docs/prompts/{filename}",
        "message": f"Prompt '{filename}' 已保存",
    }


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        prompt_id = validate_prompt_filename(prompt_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    protected = {
        "erotic-novel-policy.md", "general-novel-policy.md", "translation-policy.md",
        *FIXED_KNOWLEDGE_PROMPTS,
    }
    if prompt_id in protected:
        raise HTTPException(status_code=400, detail="默认系统 Prompt 规范不可删除")

    prompts_dir = get_prompts_dir()
    target_file = resolve_under(prompts_dir, prompt_id)
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
    if knowledge_extractor_enabled(config):
        extractor_providers = knowledge_extractor_provider_names(config)
        if extractor_providers:
            role_mapping.setdefault(extractor_providers[0], []).append("知识提取 (Knowledge Extractor)")
            for idx, extractor_fallback in enumerate(extractor_providers[1:], start=1):
                role_mapping.setdefault(extractor_fallback, []).append(f"知识提取备用 #{idx} (Knowledge Fallback)")

    def probe_single_provider(p_name: str, p_conf: dict[str, Any]) -> PreflightProviderResult:
        p_type = p_conf.get("type", "unknown")
        is_assigned = p_name in role_mapping
        role_desc = " / ".join(role_mapping.get(p_name, ["未分配"]))
        model_name = p_conf.get("model", "")
        t0 = time.time()
        timeout = 15 if is_assigned else (15 if p_type in {"antigravity", "opencode"} else 8)
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
