"""
Test suite proving refusal to merge near-miss prep methods per R027.

This test suite validates that prep_methods_match() correctly:
- Matches exact prep method strings (case-insensitive)
- REFUSES to match near-miss prep methods that are distinct culinary techniques
- Handles empty/None prep fields conservatively
- Treats size modifiers as semantically significant

Per R027: The system must NOT merge ingredients with near-miss prep methods like:
- diced vs chopped (different sizes and uniformity)
- minced vs diced (minced is paste-like, diced is cubes)
- sliced vs julienned (different shapes and precision)
- finely chopped vs roughly chopped (size modifiers matter)
"""

import pytest
from app.utils.prep import prep_methods_match


class TestExactMatching:
    """Test cases proving exact prep method matching works."""

    def test_exact_match(self):
        """Identical prep methods should match."""
        assert prep_methods_match("diced", "diced") is True

    def test_case_insensitive(self):
        """Prep matching should be case-insensitive."""
        assert prep_methods_match("Diced", "diced") is True
        assert prep_methods_match("CHOPPED", "chopped") is True
        assert prep_methods_match("Minced", "MINCED") is True

    def test_whitespace_normalization(self):
        """Leading/trailing whitespace should be stripped."""
        assert prep_methods_match(" diced ", "diced") is True
        assert prep_methods_match("chopped", "  chopped  ") is True


class TestNearMissRefusal:
    """Test cases proving R027: system REFUSES to merge near-miss prep methods."""

    def test_refuses_near_miss_diced_vs_chopped(self):
        """
        Diced and chopped are DISTINCT techniques.
        Diced = uniform cubes (~1/4 inch) for even cooking.
        Chopped = irregular pieces (1/2-3/4 inch) for texture retention.
        These should NEVER match.
        """
        assert prep_methods_match("diced", "chopped") is False

    def test_refuses_near_miss_minced_vs_diced(self):
        """
        Minced and diced are DISTINCT techniques.
        Minced = paste-like consistency for flavor distribution.
        Diced = uniform cubes for even cooking.
        These should NEVER match.
        """
        assert prep_methods_match("minced", "diced") is False

    def test_refuses_near_miss_sliced_vs_julienned(self):
        """
        Sliced and julienned are DISTINCT techniques.
        Sliced = thin cuts along length/width.
        Julienned = matchstick-thin strips (1/8" × 1/8" × 2-3").
        These should NEVER match.
        """
        assert prep_methods_match("sliced", "julienned") is False

    def test_refuses_near_miss_chopped_vs_minced(self):
        """
        Chopped and minced are DISTINCT techniques.
        Additional coverage beyond the required three near-miss pairs.
        """
        assert prep_methods_match("chopped", "minced") is False

    def test_refuses_near_miss_cubed_vs_diced(self):
        """
        Cubed and diced are DISTINCT sizes.
        Cubed = larger cubes (1/2-1 inch) for shape retention during long cooking.
        Diced = smaller cubes (~1/4 inch) for even cooking.
        """
        assert prep_methods_match("cubed", "diced") is False


class TestSizeModifiers:
    """Test cases proving size modifiers are semantically significant."""

    def test_refuses_size_modifier_difference(self):
        """
        Size modifiers distinguish distinct prep methods.
        "finely chopped" ≠ "roughly chopped" because size affects cooking time and texture.
        """
        assert prep_methods_match("finely chopped", "roughly chopped") is False

    def test_refuses_finely_chopped_vs_chopped(self):
        """
        "finely chopped" vs "chopped" are different precision levels.
        """
        assert prep_methods_match("finely chopped", "chopped") is False

    def test_refuses_roughly_chopped_vs_chopped(self):
        """
        "roughly chopped" vs "chopped" are different precision levels.
        """
        assert prep_methods_match("roughly chopped", "chopped") is False


class TestEmptyHandling:
    """Test cases for empty/None prep field handling."""

    def test_empty_matches_empty(self):
        """
        Empty prep fields should match each other.
        Both unspecified → compatible for merge.
        """
        assert prep_methods_match("", "") is True

    def test_none_matches_none(self):
        """
        None prep fields should match each other.
        Both unspecified → compatible for merge.
        """
        assert prep_methods_match(None, None) is True

    def test_empty_matches_none(self):
        """
        Empty string and None should be treated equivalently.
        Both represent "unspecified prep method."
        """
        assert prep_methods_match("", None) is True
        assert prep_methods_match(None, "") is True

    def test_empty_does_not_match_specified(self):
        """
        Unspecified prep should NOT match a specified prep method.
        One specified, one not → incompatible for merge.
        """
        assert prep_methods_match("", "diced") is False
        assert prep_methods_match("diced", "") is False
        assert prep_methods_match(None, "chopped") is False
        assert prep_methods_match("chopped", None) is False
