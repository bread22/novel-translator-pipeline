from __future__ import annotations

import concurrent.futures
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
    dual_review_enabled,
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
)
from translator.providers.registry import create_provider
from translator.web.models import PreflightProviderResult, PreflightResponse


router = APIRouter(prefix="/system", tags=["System"])


def get_config_path() -> Path:
    return Path("config.toml").resolve()


@router.get("/config")
def get_system_config() -> dict[str, Any]:
    return load_config()


@router.post("/config")
def save_system_config(config_data: dict[str, Any]) -> dict[str, Any]:
    config_file = get_config_path()
    try:
        raw_toml = tomli_w.dumps(config_data)
        config_file.write_text(raw_toml, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"保存配置文件失败: {e}")
    return {"status": "ok", "config": load_config()}


@router.post("/preflight", response_model=PreflightResponse)
def run_system_preflight() -> PreflightResponse:
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
        role_desc = " / ".join(role_mapping.get(p_name, ["未分配"]))
        model_name = p_conf.get("model", "")
        t0 = time.time()
        try:
            provider_inst = create_provider(p_name, config)
            # Run quick health check with 5s timeout
            check_res = provider_inst.health_check(timeout=5)
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
