from __future__ import annotations

from typing import Any

from scripts.codex_review import check_reviewer
from scripts.provider_translator import ProviderTranslator


class PreflightError(RuntimeError):
    def __init__(self, report: dict[str, Any]) -> None:
        self.report = report
        failed = [
            f"{item.get('name')}: {item.get('error', 'health check failed')}"
            for item in report.get("checks", [])
            if item.get("status") != "ok"
        ]
        super().__init__("启动前健康检查失败：" + "；".join(failed))


def run_preflight(translator: ProviderTranslator, timeout: int = 60) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [check_reviewer(timeout=timeout)]
    for provider in ("gemini", "murasaki-local"):
        checks.append(translator.health_check(provider, timeout=timeout))
    report = {
        "status": "ok" if all(item.get("status") == "ok" for item in checks) else "error",
        "checks": checks,
    }
    if report["status"] != "ok":
        raise PreflightError(report)
    return report
