"""GhostOps regression test: multiply() must multiply, for every sign and zero."""
import pytest

from math_utils import multiply


@pytest.mark.parametrize("a,b", [(2, 4), (3, 3), (0, 7), (-2, 5), (-3, -3), (1.5, 2)])
def test_multiply_matches_the_operator(a, b):
    assert multiply(a, b) == a * b
