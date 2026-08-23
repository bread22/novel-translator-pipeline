from __future__ import annotations

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
from translator.pipeline.preflight import run_preflight
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

    role_mapping: dict[str, list[str]] = {}
    if primary:
        role_mapping.setdefault(primary, []).append("主译 (Primary)")
    for idx, fb in enumerate(fallbacks, start=1):
        role_mapping.setdefault(fb, []).append(f"备用 #{idx} (Fallback)")
    if reviewer:
        role_mapping.setdefault(reviewer, []).append("一致性审阅者 (Reviewer)")

    results: list[PreflightProviderResult] = []
    all_passed = True

    for p_name, p_conf in providers_config.items():
        p_type = p_conf.get("type", "unknown")
        role_desc = " / ".join(role_mapping.get(p_name, ["未分配"]))
        model_name = p_conf.get("model", "")

        t0 = time.time()
        try:
            provider_inst = create_provider(p_name, config)
            # Run simple test check
            health_ok = provider_inst.health_check()
            latency = round((time.time() - t0) * 1000, 1)

            if health_ok:
                results.append(
                    PreflightProviderResult(
                        provider=p_name,
                        type=p_type,
                        role=role_desc,
                        status="ok",
                        latency_ms=latency,
                        model=model_name,
                        message="连通性正常，模型响应就绪",
                    )
                )
            else:
                all_passed = False
                results.append(
                    PreflightProviderResult(
                        provider=p_name,
                        type=p_type,
                        role=role_desc,
                        status="failed",
                        latency_ms=latency,
                        model=model_name,
                        message="健康预检失败，模型未响应标准响应",
                    )
                )
        except Exception as exc:
            all_passed = False
            latency = round((time.time() - t0) * 1000, 1)
            results.append(
                PreflightProviderResult(
                    provider=p_name,
                    type=p_type,
                    role=role_desc,
                    status="failed",
                    latency_ms=latency,
                    model=model_name,
                    message=f"连接异常: {exc}",
                )
            )

    return PreflightResponse(all_passed=all_passed, results=results)
