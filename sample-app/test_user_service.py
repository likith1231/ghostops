from user_service import display_name


def test_display_name():
    assert display_name({"name": "  Ada  "}) == "Ada"


def test_display_name_empty_payload():
    assert display_name(None) == "anonymous"


def test_display_name_missing_name():
    assert display_name({}) == "anonymous"
