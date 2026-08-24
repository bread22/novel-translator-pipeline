from scripts.check_frontend_api_contract import check_contract


def test_frontend_interfaces_match_openapi_properties() -> None:
    assert check_contract() == []
