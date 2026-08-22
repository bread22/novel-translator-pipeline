from __future__ import annotations

from typing import Any

from translator.core.config import (
    dual_review_enabled,
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
    secondary_reviewer_name,
)
from translator.providers.translator import ProviderTranslator
from translator.review.reviewer import check_reviewer


class PreflightError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        failed = [
            f"{item.get('name')}: {item.get('error', 'health check failed')}"
            for item in report.get("checks", [])
            if item.get("status") != "ok"
        ]
        super().__init__("启动前健康检查失败：" + "；".join(failed))


def run_preflight(
    translator: ProviderTranslator,
    timeout: int | None = None,
    *,
    primary_translator: str | None = None,
    fallback_translators: list[str] | str | None = None,
    fallback_translator: str | None = None,
    secondary_fallback_translator: str | None = None,
    reviewer: str | None = None,
    secondary_reviewer: str | None = None,
    dual_review: bool | None = None,
) -> dict[str, Any]:
    config = load_config()
    eff_timeout = timeout or int(config.get("pipeline", {}).get("health_check_timeout", 120))
    primary = primary_translator or primary_translator_name(config)
    rev = reviewer or reviewer_name(config)
    sec_rev = secondary_reviewer or secondary_reviewer_name(config)
    if dual_review is not None:
        is_dual = dual_review
    elif reviewer is not None and secondary_reviewer is None:
        is_dual = False
    else:
        is_dual = dual_review_enabled(config)

    fbs: list[str] = []
    if fallback_translators:
        if isinstance(fallback_translators, str):
            fbs = [item.strip() for item in fallback_translators.split(",") if item.strip()]
        else:
            fbs = list(fallback_translators)
    elif fallback_translator:
        fbs = [fallback_translator]
        if secondary_fallback_translator:
            fbs.append(secondary_fallback_translator)
    else:
        fbs = fallback_translators_names(config)

    checks: list[dict[str, Any]] = [check_reviewer(timeout=eff_timeout, backend=rev)]
    if is_dual and sec_rev and sec_rev != rev:
        checks.append(check_reviewer(timeout=eff_timeout, backend=sec_rev))
    checked_providers: set[str] = set()

    for provider in [primary] + fbs:
        if provider and provider not in checked_providers:
            checked_providers.add(provider)
            checks.append(translator.health_check(provider, timeout=eff_timeout))

    report = {
        "status": "ok" if all(item.get("status") == "ok" for item in checks) else "error",
        "checks": checks,
    }
    if report["status"] != "ok":
        raise PreflightError(report)
    return report
