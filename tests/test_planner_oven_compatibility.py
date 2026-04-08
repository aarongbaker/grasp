import uuid

from app.graph.nodes.generator import _score_menu_oven_compatibility
from app.models.recipe import Ingredient, RawRecipe, RecipeProvenance


def _recipe(name: str, *steps: str) -> RawRecipe:
    return RawRecipe(
        name=name,
        description=f"{name} fixture",
        servings=4,
        cuisine="Test",
        estimated_total_minutes=90,
        ingredients=[Ingredient(name="fixture ingredient", quantity="1 unit")],
        steps=list(steps),
        provenance=RecipeProvenance(kind="generated"),
    )


def test_single_oven_prefers_compatible_temperatures_over_long_braise_plus_hot_dessert() -> None:
    compatible_menu = [
        _recipe(
            "Chicken Thigh Roast",
            "Season the chicken.",
            "Roast in a 375°F oven for 45 minutes.",
            "Rest before serving.",
        ),
        _recipe(
            "Apple Crumble",
            "Mix the topping.",
            "Bake in a 380°F oven until browned.",
            "Cool briefly before serving.",
        ),
        _recipe(
            "Shaved Fennel Salad",
            "Slice the fennel.",
            "Dress with lemon.",
            "Hold chilled until serving.",
        ),
    ]
    incompatible_menu = [
        _recipe(
            "Long Short Rib Braise",
            "Brown the ribs.",
            "Cover and braise in a 300°F oven for 3 hours.",
            "Rest before serving.",
        ),
        _recipe(
            "Chocolate Fondant",
            "Mix the batter.",
            "Bake in a 425°F oven for 12 minutes.",
            "Serve immediately.",
        ),
        _recipe(
            "Green Beans Almondine",
            "Blanch the beans.",
            "Finish in butter.",
            "Season and serve.",
        ),
    ]

    compatible_score = _score_menu_oven_compatibility(recipes=compatible_menu, has_second_oven=False)
    incompatible_score = _score_menu_oven_compatibility(recipes=incompatible_menu, has_second_oven=False)

    assert compatible_score["score"] < incompatible_score["score"]
    assert compatible_score["incompatible_pairs"] == []
    assert incompatible_score["incompatible_pairs"] == [
        ("Long Short Rib Braise", "Chocolate Fondant", 300, 425)
    ]


def test_tolerance_window_treats_fifteen_degree_spread_as_compatible() -> None:
    menu = [
        _recipe("Roast Chicken", "Prep the bird.", "Roast in a 375°F oven for 50 minutes.", "Rest."),
        _recipe("Pear Tart", "Prepare the tart.", "Bake in a 390°F oven for 35 minutes.", "Cool."),
    ]

    score = _score_menu_oven_compatibility(recipes=menu, has_second_oven=False)

    assert score["tolerance_f"] == 15
    assert score["incompatible_pairs"] == []
    assert score["score"] == 25


def test_single_oven_prefers_one_oven_heavy_recipe_when_other_dishes_have_no_parseable_oven_temp() -> None:
    menu = [
        _recipe(
            "Braised Lamb Shoulder",
            "Brown the lamb.",
            "Cook in a low oven until tender.",
            "Rest before pulling.",
        ),
        _recipe(
            "Citrus Salad",
            "Segment the citrus.",
            "Dress with olive oil.",
            "Serve chilled.",
        ),
        _recipe(
            "Whipped Ricotta Toasts",
            "Whip the ricotta.",
            "Toast bread on the stovetop griddle.",
            "Top and serve.",
        ),
    ]

    score = _score_menu_oven_compatibility(recipes=menu, has_second_oven=False)

    assert score["temperatures_by_recipe"]["Braised Lamb Shoulder"] == [312]
    assert score["score"] == 0
    assert set(score["missing_temperature_recipes"]) == {"Citrus Salad", "Whipped Ricotta Toasts"}


def test_second_oven_downranks_conflicts_less_aggressively() -> None:
    menu = [
        _recipe("Short Rib Braise", "Sear the ribs.", "Braise in a 300°F oven.", "Rest."),
        _recipe("Molten Cake", "Mix the batter.", "Bake in a 425°F oven.", "Serve."),
    ]

    single_oven = _score_menu_oven_compatibility(recipes=menu, has_second_oven=False)
    dual_oven = _score_menu_oven_compatibility(recipes=menu, has_second_oven=True)

    assert single_oven["incompatible_pairs"] == dual_oven["incompatible_pairs"]
    assert dual_oven["score"] < single_oven["score"]
    assert dual_oven["score"] == 0


def test_mixed_origin_anchor_is_scored_with_generated_complements() -> None:
    anchor_recipe = RawRecipe(
        name="Anchored Duck Confit",
        description="Authored anchor",
        servings=4,
        cuisine="French",
        estimated_total_minutes=180,
        ingredients=[Ingredient(name="duck legs", quantity="4")],
        steps=[
            "Cure the duck overnight.",
            "Slow-roast in a 325°F oven until tender.",
            "Crisp before serving.",
        ],
        provenance=RecipeProvenance(kind="library_authored", recipe_id=str(uuid.uuid4())),
    )
    complements = [
        _recipe("Potato Galette", "Layer the potatoes.", "Bake in a 330°F oven until golden.", "Rest briefly."),
        _recipe("Bitter Greens Salad", "Wash the greens.", "Dress lightly.", "Serve."),
    ]

    score = _score_menu_oven_compatibility(recipes=[anchor_recipe, *complements], has_second_oven=False)

    assert score["incompatible_pairs"] == []
    assert score["temperatures_by_recipe"]["Anchored Duck Confit"] == [325]
    assert score["temperatures_by_recipe"]["Potato Galette"] == [330]
    assert score["score"] == 25
