from __future__ import annotations

from typing import Any

from translator.core.config import load_config
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
    fallback_translator: str | None = None,
    reviewer: str | None = None,
) -> dict[str, Any]:
    roles = load_config()["roles"]
    primary_translator = primary_translator or str(roles["primary_translator"])
    fallback_translator = fallback_translator or str(roles["fallback_translator"])
    reviewer = reviewer or str(roles["reviewer"])
    checks: list[dict[str, Any]] = [check_reviewer(timeout=timeout, backend=reviewer)]
    for provider in dict.fromkeys((primary_translator, fallback_translator)):
        checks.append(translator.health_check(provider, timeout=timeout))
    report = {
        "status": "ok" if all(item.get("status") == "ok" for item in checks) else "error",
        "checks": checks,
    }
    if report["status"] != "ok":
        raise PreflightError(report)
    return report
