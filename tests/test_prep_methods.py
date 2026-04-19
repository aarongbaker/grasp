"""
Unit tests for prep method matching utility.

Per R027, these tests prove the system correctly refuses to merge ingredients
with different prep methods (near-misses like "diced" vs. "chopped").
"""

import pytest

from app.utils.prep import prep_methods_match


class TestPrepMethodsMatch:
    """Test exact string matching for preparation methods."""

    def test_exact_match_same_case(self):
        """Identical prep methods should match."""
        assert prep_methods_match("diced", "diced") is True
        assert prep_methods_match("chopped", "chopped") is True
        assert prep_methods_match("minced", "minced") is True

    def test_exact_match_case_insensitive(self):
        """Case differences should be ignored."""
        assert prep_methods_match("Diced", "diced") is True
        assert prep_methods_match("CHOPPED", "chopped") is True
        assert prep_methods_match("Minced", "MINCED") is True

    def test_whitespace_normalization(self):
        """Leading/trailing whitespace should be stripped."""
        assert prep_methods_match("  diced  ", "diced") is True
        assert prep_methods_match("chopped", " chopped ") is True
        assert prep_methods_match("  sliced  ", "  sliced  ") is True

    def test_refuses_near_misses_diced_vs_chopped(self):
        """R027: Must refuse to match "diced" vs "chopped" (distinct techniques)."""
        assert prep_methods_match("diced", "chopped") is False

    def test_refuses_near_misses_minced_vs_diced(self):
        """R027: Must refuse to match "minced" vs "diced" (different consistencies)."""
        assert prep_methods_match("minced", "diced") is False

    def test_refuses_near_misses_sliced_vs_julienned(self):
        """R027: Must refuse to match "sliced" vs "julienned" (different cuts)."""
        assert prep_methods_match("sliced", "julienned") is False

    def test_refuses_size_modifier_differences(self):
        """Size modifiers are semantically significant."""
        assert prep_methods_match("roughly chopped", "finely chopped") is False
        assert prep_methods_match("chopped", "finely chopped") is False
        assert prep_methods_match("finely chopped", "roughly chopped") is False

    def test_empty_strings_match(self):
        """Both unspecified prep methods should be considered equivalent."""
        assert prep_methods_match("", "") is True

    def test_none_values_match(self):
        """Both None prep methods should be considered equivalent."""
        assert prep_methods_match(None, None) is True

    def test_empty_and_none_match(self):
        """Empty string and None should be treated the same (both unspecified)."""
        assert prep_methods_match("", None) is True
        assert prep_methods_match(None, "") is True

    def test_specified_vs_unspecified_no_match(self):
        """Specified prep should not match unspecified (empty/None)."""
        assert prep_methods_match("diced", "") is False
        assert prep_methods_match("diced", None) is False
        assert prep_methods_match("", "chopped") is False
        assert prep_methods_match(None, "minced") is False

    def test_complex_prep_strings(self):
        """Complex prep strings with multiple words must match exactly."""
        assert prep_methods_match("cut into 1-inch cubes", "cut into 1-inch cubes") is True
        assert prep_methods_match("cut into 1-inch cubes", "cut into 2-inch cubes") is False
        assert prep_methods_match("thinly sliced", "thickly sliced") is False
