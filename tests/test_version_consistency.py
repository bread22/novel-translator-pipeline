from scripts.check_version_consistency import check_versions


def test_release_versions_are_consistent() -> None:
    report = check_versions()
    assert report["status"] == "ok", report
