from __future__ import annotations

from typing import Any

from translator.core.config import (
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
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
    timeout: int = 60,
    *,
    primary_translator: str | None = None,
    fallback_translators: list[str] | str | None = None,
    fallback_translator: str | None = None,
    secondary_fallback_translator: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    config = load_config()
    primary = primary_translator or primary_translator_name(config)
    rev = reviewer or reviewer_name(config)

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

    checks: list[dict[str, Any]] = [check_reviewer(timeout=timeout, backend=rev)]
    checked_providers: set[str] = set()

    for provider in [primary] + fbs:
        if provider and provider not in checked_providers:
            checked_providers.add(provider)
            checks.append(translator.health_check(provider, timeout=timeout))

    report = {
        "status": "ok" if all(item.get("status") == "ok" for item in checks) else "error",
        "checks": checks,
    }
    if report["status"] != "ok":
        raise PreflightError(report)
    return report
