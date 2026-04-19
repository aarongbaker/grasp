"""
Preparation method matching utilities for ingredient merge logic.

This module establishes the policy that culinary prep methods are DISTINCT TECHNIQUES,
not interchangeable synonyms. The scheduler uses exact string matching (case-insensitive)
to determine if two ingredients with different prep methods can be merged.

Per requirements R002 and R005, the system errs on the side of separation rather than
speculative merging. Conservative exact matching prevents incorrect merges that would
produce wrong cooking instructions or timing.

## Common Prep Methods (NOT Synonyms)

The following are DISTINCT culinary techniques with materially different outcomes.
They should NEVER be normalized or treated as interchangeable:

- **diced**: Uniform cubes (~1/4 inch or smaller) for even cooking and consistent texture.
  Used when uniform size matters for even heat distribution (e.g., diced onions for sofrito).

- **chopped**: Irregular pieces (1/2 to 3/4 inch) for texture retention and visual appeal.
  Larger and less uniform than diced. Used when rustic texture is desired (e.g., chopped
  tomatoes for salsa).

- **minced**: Very fine, paste-like consistency for maximum flavor distribution. Often
  near-pulverized. Used when ingredient should blend into the dish (e.g., minced garlic
  in sauce).

- **sliced**: Thin, uniform cuts along the length or width of the ingredient. Used for
  presentation or when thinness matters for cooking time (e.g., sliced mushrooms for sauté).

- **julienned**: Matchstick-thin strips (1/8 inch × 1/8 inch × 2-3 inches). Precision cut
  for specific textures and presentation (e.g., julienned vegetables for stir-fry).

- **roughly chopped**: Large, very irregular pieces. Minimal knife work. Used when
  ingredient will be strained out or blended (e.g., roughly chopped vegetables for stock).

- **finely chopped**: Smaller than chopped but larger than minced. Semi-uniform pieces
  (~1/8 inch). Used when small pieces are needed but paste-like texture is not desired
  (e.g., finely chopped herbs for garnish).

- **cubed**: Larger uniform cubes (~1/2 to 1 inch). Used when ingredient needs to hold
  shape during long cooking (e.g., cubed potatoes for stew).

- **shredded**: Long thin strips created by grating or slicing. Used for quick-cooking
  ingredients or melting (e.g., shredded cheese, shredded cabbage for coleslaw).

- **grated**: Very fine particles created by a grater. Finer than shredded. Used for
  maximum surface area or quick dissolution (e.g., grated Parmesan, grated ginger).

**Size modifiers matter**: "finely chopped" ≠ "roughly chopped" ≠ "chopped". 
The size modifier is semantically significant for cooking time and texture.

**Empty/unspecified prep**: If both ingredients have empty or None prep method strings,
they should be considered equivalent (both unspecified → compatible for merge).

## References

- Culinary prep method distinctions: https://farmersmarketsnm.org/chop-mince-or-dice-20-frequently-used-recipe-terms-to-learn-today/
- Chopping vs. dicing impact on cooking: https://misen.com/blogs/news/chopped-vs-diced
"""


def prep_methods_match(prep1: str | None, prep2: str | None) -> bool:
    """
    Return True only if preparation methods match exactly (case-insensitive).

    This function implements conservative exact-match comparison per requirements
    R002 (high-confidence matching) and R005 (keep prep tasks separate rather than
    speculative merging).

    **Design rationale**: Culinary prep methods like "diced," "chopped," "minced,"
    and "sliced" are distinct techniques with materially different outcomes. Dicing
    produces uniform cubes for even cooking. Chopping creates irregular pieces for
    texture retention. Mincing produces paste-like consistency. Substituting one for
    another changes cooking time, texture, and final dish quality.

    This function does NOT perform synonym normalization, fuzzy matching, or LLM-based
    similarity checks. It performs deterministic string comparison only.

    Args:
        prep1: First preparation method string (e.g., "diced", "chopped"). May be None.
        prep2: Second preparation method string. May be None.

    Returns:
        True if prep methods match exactly (after case-insensitive comparison and
        whitespace stripping), or if both are empty/None (both unspecified).
        False if prep methods differ.

    Examples:
        >>> prep_methods_match("diced", "diced")
        True
        >>> prep_methods_match("Diced", "diced")  # case-insensitive
        True
        >>> prep_methods_match("diced", "chopped")  # distinct techniques
        False
        >>> prep_methods_match("minced", "diced")
        False
        >>> prep_methods_match("sliced", "julienned")
        False
        >>> prep_methods_match("roughly chopped", "finely chopped")  # size matters
        False
        >>> prep_methods_match("", "")  # both unspecified
        True
        >>> prep_methods_match(None, None)  # both unspecified
        True
        >>> prep_methods_match("diced", "")  # one specified, one not
        False
    """
    # Normalize None to empty string for consistent comparison
    p1 = (prep1 or "").strip().lower()
    p2 = (prep2 or "").strip().lower()

    # Exact match (both empty strings match)
    return p1 == p2
