"""GhostOps regression test: empty upstream payloads must not crash display_name()."""
import pytest

from user_service import display_name


@pytest.mark.parametrize("payload", [None, {}, {"name": None}, {"name": ""}])
def test_display_name_survives_empty_payloads(payload):
    assert display_name(payload) == "anonymous"
