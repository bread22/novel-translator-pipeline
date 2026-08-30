from translator.pipeline.chapter_pipeline import IterativePipeline


def test_projection_uses_only_verified_applied_replacements():
    window = {"items": [{"id": "p1", "translated": "现译"}, {"id": "p2", "translated": "现译二"}]}
    review = {"fixes": [
        {"id": "p1", "replacement": "已接受", "apply_state": "applied"},
        {"id": "p2", "replacement": "仅建议", "apply_state": "not_applied"},
    ]}
    projected = IterativePipeline._project_window_fixes(window, review, apply=True)
    assert [i["translated"] for i in projected["items"]] == ["已接受", "现译二"]
    unchanged = IterativePipeline._project_window_fixes(window, review, apply=False)
    assert [i["translated"] for i in unchanged["items"]] == ["现译", "现译二"]
