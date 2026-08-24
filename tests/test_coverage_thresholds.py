from scripts.check_coverage_thresholds import GROUPS, check_coverage


def test_coverage_threshold_checker_reports_group_failure() -> None:
    files = {}
    for _name, (_threshold, patterns) in GROUPS.items():
        path = next(pattern for pattern in patterns if not pattern.endswith("/")) if any(not pattern.endswith("/") for pattern in patterns) else f"{patterns[0]}module.py"
        files[path] = {"summary": {"num_statements": 10, "covered_lines": 1}}
    reports, failures = check_coverage({"files": files})
    assert len(reports) == 3 and failures
