"""
tests/fixtures/recipes.py
Hardcoded realistic fixture data for three dinner dishes.
All step_ids are globally unique and consistent across recipes.py and schedules.py.

CRITICAL: step_ids must follow the format {recipe_slug}_step_{n} exactly.
Schedules.py references these same IDs for depends_on edges and DAG edges.
Any mismatch will be caught by mock_validator.py's real depends_on validator.

Three dishes chosen to demonstrate PASSIVE parallelism:
  Short Rib Braise  — 3-hour PASSIVE braise window enables other steps to run
  Pommes Puree      — runs during the braise (STOVETOP then HANDS)
  Chocolate Fondant — batter made ahead, final bake near service

This menu is the Phase 6 known-correct answer validation baseline.
"""

import uuid

from app.models.authored_recipe import (
    AuthoredRecipeCreate,
    AuthoredRecipeDependency,
    AuthoredRecipeHoldGuidance,
    AuthoredRecipeReheatGuidance,
    AuthoredRecipeStep,
    AuthoredRecipeStorageGuidance,
    AuthoredRecipeYield,
    build_authored_step_id,
)
from app.models.enums import Resource
from app.models.recipe import EnrichedRecipe, Ingredient, RawRecipe, RecipeProvenance, RecipeStep

# ── Step IDs ─────────────────────────────────────────────────────────────────
# Defined as constants so schedules.py can import them instead of using magic strings.
# This eliminates typo bugs in depends_on references across both fixture files.

# Short Rib steps
SR_STEP_1 = "short_rib_step_1"  # sear — STOVETOP, 20 min
SR_STEP_2 = "short_rib_step_2"  # braising liquid — HANDS, 10 min
SR_STEP_3 = "short_rib_step_3"  # braise — OVEN, 150-180 min (PASSIVE parallelism during window)
SR_STEP_4 = "short_rib_step_4"  # rest — PASSIVE, 15 min

# Pommes Puree steps
PP_STEP_1 = "pommes_puree_step_1"  # boil potatoes — STOVETOP, 30 min
PP_STEP_2 = "pommes_puree_step_2"  # rice and dry — HANDS, 10 min
PP_STEP_3 = "pommes_puree_step_3"  # finish with butter — HANDS, 15 min

# Chocolate Fondant steps
CF_STEP_1 = "fondant_step_1"  # melt chocolate + butter — STOVETOP, 10 min
CF_STEP_2 = "fondant_step_2"  # make batter — HANDS, 15 min
CF_STEP_3 = "fondant_step_3"  # butter ramekins + fill — HANDS, 10 min
CF_STEP_4 = "fondant_step_4"  # chill — PASSIVE, 30 min (can_be_done_ahead)
CF_STEP_5 = "fondant_step_5"  # bake — OVEN, 12 min (duration_max=14)


# ── Raw Recipes ───────────────────────────────────────────────────────────────

RAW_SHORT_RIBS = RawRecipe(
    name="Braised Short Ribs",
    description="Low-and-slow bone-in short ribs in a red wine braise. "
    "Rich, deeply flavoured, ideal for a dinner party.",
    servings=4,
    cuisine="French-American",
    estimated_total_minutes=210,
    course="entree",
    ingredients=[
        Ingredient(name="bone-in short ribs", quantity="2kg", preparation="trimmed of excess fat"),
        Ingredient(name="red wine", quantity="500ml", preparation="Burgundy or Côtes du Rhône"),
        Ingredient(name="beef stock", quantity="500ml"),
        Ingredient(name="carrot", quantity="2 large", preparation="roughly chopped"),
        Ingredient(name="celery", quantity="3 stalks", preparation="roughly chopped"),
        Ingredient(name="onion", quantity="2 large", preparation="roughly chopped"),
        Ingredient(name="garlic", quantity="6 cloves", preparation="smashed"),
        Ingredient(name="tomato paste", quantity="2 tbsp"),
        Ingredient(name="thyme", quantity="4 sprigs"),
        Ingredient(name="bay leaves", quantity="2"),
        Ingredient(name="neutral oil", quantity="3 tbsp"),
        Ingredient(name="salt and pepper", quantity="to taste"),
    ],
    steps=[
        "Season short ribs generously with salt and pepper. Heat oil in a heavy Dutch oven over high heat. Sear ribs on all sides until deeply browned, about 4-5 minutes per side. Work in batches. Transfer to a plate.",
        "Reduce heat to medium. Add carrot, celery, and onion. Cook until softened and lightly caramelised, about 8 minutes. Add garlic and tomato paste. Cook 2 minutes more.",
        "Add wine, scraping up any browned bits. Add stock, thyme, bay leaves. Bring to a boil. Return ribs to pot — they should be almost submerged. Cover tightly and braise in a 150°C oven for 2.5-3 hours until meat is falling-off-the-bone tender.",
        "Remove ribs carefully. Rest on a warm plate, loosely tented with foil, for 15 minutes before serving.",
    ],
)

RAW_POMMES_PUREE = RawRecipe(
    name="Pommes Puree",
    description="Robuchon-style potato purée. More butter than you think. Silky, rich.",
    servings=4,
    cuisine="French",
    estimated_total_minutes=55,
    course="side",
    ingredients=[
        Ingredient(name="Yukon Gold potatoes", quantity="1kg", preparation="peeled, cut into even chunks"),
        Ingredient(name="unsalted butter", quantity="200g", preparation="cold, cubed"),
        Ingredient(name="whole milk", quantity="200ml", preparation="warmed"),
        Ingredient(name="salt", quantity="to taste"),
        Ingredient(name="white pepper", quantity="to taste"),
    ],
    steps=[
        "Cover potatoes with cold salted water. Bring to a boil. Cook until completely tender when pierced with a knife tip, about 25-30 minutes.",
        "Drain potatoes thoroughly. Pass through a fine-mesh ricer or food mill while still hot — never use a food processor. Return to the pot over low heat and dry out for 2-3 minutes, stirring.",
        "Off heat, beat in cold butter cube by cube until fully incorporated. Slowly stream in warm milk until desired consistency. Season with salt and white pepper. Pass through a fine sieve for silk-smooth result.",
    ],
)

RAW_CHOCOLATE_FONDANT = RawRecipe(
    name="Chocolate Fondant",
    description="Individual molten chocolate puddings. Batter made ahead. "
    "12-minute bake at service. Molten centre is non-negotiable.",
    servings=4,
    cuisine="French-British",
    estimated_total_minutes=75,
    course="dessert",
    ingredients=[
        Ingredient(name="dark chocolate 70%", quantity="200g", preparation="roughly chopped"),
        Ingredient(name="unsalted butter", quantity="150g", preparation="cubed, plus extra for ramekins"),
        Ingredient(name="eggs", quantity="4 large"),
        Ingredient(name="egg yolks", quantity="4"),
        Ingredient(name="caster sugar", quantity="100g"),
        Ingredient(name="plain flour", quantity="50g"),
        Ingredient(name="cocoa powder", quantity="for dusting ramekins"),
    ],
    steps=[
        "Melt chocolate and butter together in a heatproof bowl set over simmering water. Stir until smooth. Remove from heat and allow to cool slightly.",
        "Whisk eggs, yolks, and sugar together until pale and slightly thickened. Fold in chocolate mixture, then sift in flour and fold until just combined.",
        "Butter 4 ramekins generously. Dust with cocoa powder, tapping out excess. Fill each with batter to within 5mm of the rim.",
        "Refrigerate for at least 30 minutes and up to 24 hours. The cold rest is essential for a clean molten centre.",
        "Bake at 200°C (fan 180°C) for 12-14 minutes — the edges should be set but the centre should wobble. Serve immediately.",
    ],
)


# ── Enriched Recipes ──────────────────────────────────────────────────────────
# Flat string steps converted to structured RecipeStep objects.
# depends_on references use the step ID constants defined above — guaranteed consistent.

ENRICHED_SHORT_RIBS = EnrichedRecipe(
    source=RAW_SHORT_RIBS,
    steps=[
        RecipeStep(
            step_id=SR_STEP_1,
            description="Season and sear short ribs on all sides until deeply browned. Work in batches — do not crowd the pan.",
            duration_minutes=20,
            duration_max=None,
            depends_on=[],
            resource=Resource.STOVETOP,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=SR_STEP_2,
            description="Sweat aromatics (carrot, celery, onion, garlic), add tomato paste. Deglaze with red wine, add stock and herbs.",
            duration_minutes=10,
            duration_max=None,
            depends_on=[SR_STEP_1],
            resource=Resource.HANDS,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=SR_STEP_3,
            description="Braise short ribs in a 150°C oven, covered, for 2.5-3 hours until falling-off-the-bone tender.",
            duration_minutes=150,
            duration_max=180,
            depends_on=[SR_STEP_2],
            resource=Resource.OVEN,
            can_be_done_ahead=True,
            prep_ahead_window="up to 2 days in advance",
            prep_ahead_notes="Cool in braising liquid. Reheat gently, basting with strained jus.",
        ),
        RecipeStep(
            step_id=SR_STEP_4,
            description="Rest ribs, loosely tented with foil, before serving.",
            duration_minutes=15,
            duration_max=None,
            depends_on=[SR_STEP_3],
            resource=Resource.PASSIVE,
            can_be_done_ahead=False,
        ),
    ],
    rag_sources=["chunk_001", "chunk_045", "chunk_089"],
    chef_notes="The braise liquid reduces to an intensely flavoured jus. Strain and reduce separately if needed.",
    techniques_used=["maillard reaction", "braising", "fond development"],
)

ENRICHED_POMMES_PUREE = EnrichedRecipe(
    source=RAW_POMMES_PUREE,
    steps=[
        RecipeStep(
            step_id=PP_STEP_1,
            description="Boil potatoes in well-salted water from cold start until completely tender, 25-30 minutes.",
            duration_minutes=30,
            duration_max=None,
            depends_on=[],
            resource=Resource.STOVETOP,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=PP_STEP_2,
            description="Drain and rice potatoes while still hot. Return to pot over low heat to steam-dry for 2-3 minutes.",
            duration_minutes=10,
            duration_max=None,
            depends_on=[PP_STEP_1],
            resource=Resource.HANDS,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=PP_STEP_3,
            description="Beat in cold butter cube by cube. Stream in warm milk. Season. Pass through fine sieve.",
            duration_minutes=15,
            duration_max=None,
            depends_on=[PP_STEP_2],
            resource=Resource.HANDS,
            can_be_done_ahead=False,
        ),
    ],
    rag_sources=["chunk_102", "chunk_103"],
    chef_notes="Robuchon technique: the butter ratio is 1:5 butter to potato by weight. Do not deviate.",
    techniques_used=["ricing", "emulsification"],
)

ENRICHED_CHOCOLATE_FONDANT = EnrichedRecipe(
    source=RAW_CHOCOLATE_FONDANT,
    steps=[
        RecipeStep(
            step_id=CF_STEP_1,
            description="Melt dark chocolate and butter together over a bain-marie. Stir until smooth. Cool slightly.",
            duration_minutes=10,
            duration_max=None,
            depends_on=[],
            resource=Resource.STOVETOP,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=CF_STEP_2,
            description="Whisk eggs, yolks, and sugar until pale. Fold in chocolate. Sift in flour and fold.",
            duration_minutes=15,
            duration_max=None,
            depends_on=[CF_STEP_1],
            resource=Resource.HANDS,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=CF_STEP_3,
            description="Butter and cocoa-dust 4 ramekins. Fill to within 5mm of rim.",
            duration_minutes=10,
            duration_max=None,
            depends_on=[CF_STEP_2],
            resource=Resource.HANDS,
            can_be_done_ahead=False,
        ),
        RecipeStep(
            step_id=CF_STEP_4,
            description="Refrigerate filled ramekins for at least 30 minutes.",
            duration_minutes=30,
            duration_max=None,
            depends_on=[CF_STEP_3],
            resource=Resource.PASSIVE,
            can_be_done_ahead=True,
            prep_ahead_window="up to 24 hours in advance",
            prep_ahead_notes="Keep refrigerated. Bake straight from fridge — cold ramekin ensures molten centre.",
        ),
        RecipeStep(
            step_id=CF_STEP_5,
            description="Bake at 200°C (fan 180°C) for 12-14 minutes. Edges set, centre wobbles. Serve immediately.",
            duration_minutes=12,
            duration_max=14,
            depends_on=[CF_STEP_4],
            resource=Resource.OVEN,
            can_be_done_ahead=False,
        ),
    ],
    rag_sources=["chunk_201", "chunk_202"],
    chef_notes="Baking time depends on ramekin material and oven calibration. Test one fondant first.",
    techniques_used=["bain-marie", "folding", "mise en place"],
)


# ── Cyclic fixture data (Phase 6 fatal error test) ──────────────────────────
# Each recipe has a circular dependency: step_1 → step_2 → step_1.
# EnrichedRecipe.model_validator allows this (it checks reference existence,
# NOT acyclicity). The DAG builder catches cycles via NetworkX.

CYCLIC_STEPS_SHORT_RIBS = [
    RecipeStep(
        step_id="short_rib_step_1",
        description="Sear ribs",
        duration_minutes=20,
        depends_on=["short_rib_step_2"],  # ← creates cycle
        resource=Resource.STOVETOP,
    ),
    RecipeStep(
        step_id="short_rib_step_2",
        description="Braise ribs",
        duration_minutes=150,
        depends_on=["short_rib_step_1"],  # ← creates cycle
        resource=Resource.OVEN,
    ),
]

CYCLIC_STEPS_POMMES_PUREE = [
    RecipeStep(
        step_id="pommes_puree_step_1",
        description="Boil potatoes",
        duration_minutes=30,
        depends_on=["pommes_puree_step_2"],
        resource=Resource.STOVETOP,
    ),
    RecipeStep(
        step_id="pommes_puree_step_2",
        description="Rice potatoes",
        duration_minutes=10,
        depends_on=["pommes_puree_step_1"],
        resource=Resource.HANDS,
    ),
]

CYCLIC_STEPS_FONDANT = [
    RecipeStep(
        step_id="fondant_step_1",
        description="Melt chocolate",
        duration_minutes=10,
        depends_on=["fondant_step_2"],
        resource=Resource.STOVETOP,
    ),
    RecipeStep(
        step_id="fondant_step_2",
        description="Make batter",
        duration_minutes=15,
        depends_on=["fondant_step_1"],
        resource=Resource.HANDS,
    ),
]


# ── Finish-Together Test Fixtures ─────────────────────────────────────────────
# Three recipes with different cooking durations to test finish-together scheduling.
# Step IDs must match those defined in schedules.py (FT_*_PREP, FT_*_COOK).

# Step ID constants for finish-together fixtures
FT_A_PREP = "ft_recipe_a_prep"
FT_A_COOK = "ft_recipe_a_cook"
FT_B_PREP = "ft_recipe_b_prep"
FT_B_COOK = "ft_recipe_b_cook"
FT_C_PREP = "ft_recipe_c_prep"
FT_C_COOK = "ft_recipe_c_cook"

RAW_FT_RECIPE_A = RawRecipe(
    name="Recipe A Long Braise",
    description="A dish with a long braise time for finish-together testing.",
    servings=4,
    cuisine="Test",
    estimated_total_minutes=210,
    ingredients=[Ingredient(name="ingredient", quantity="1")],
    steps=["prep", "cook"],
)

RAW_FT_RECIPE_B = RawRecipe(
    name="Recipe B Quick Saute",
    description="A quick sauté dish for finish-together testing.",
    servings=4,
    cuisine="Test",
    estimated_total_minutes=75,
    ingredients=[Ingredient(name="ingredient", quantity="1")],
    steps=["prep", "cook"],
)

RAW_FT_RECIPE_C = RawRecipe(
    name="Recipe C Medium Roast",
    description="A medium roast dish for finish-together testing.",
    servings=4,
    cuisine="Test",
    estimated_total_minutes=80,
    ingredients=[Ingredient(name="ingredient", quantity="1")],
    steps=["prep", "cook"],
)

ENRICHED_FT_RECIPE_A = EnrichedRecipe(
    source=RAW_FT_RECIPE_A,
    steps=[
        RecipeStep(
            step_id=FT_A_PREP,
            description="Prepare ingredients for long braise",
            duration_minutes=30,
            depends_on=[],
            resource=Resource.HANDS,
        ),
        RecipeStep(
            step_id=FT_A_COOK,
            description="Braise for 3 hours in oven",
            duration_minutes=180,
            depends_on=[FT_A_PREP],
            resource=Resource.OVEN,
        ),
    ],
)

ENRICHED_FT_RECIPE_B = EnrichedRecipe(
    source=RAW_FT_RECIPE_B,
    steps=[
        RecipeStep(
            step_id=FT_B_PREP,
            description="Prepare ingredients for quick sauté",
            duration_minutes=15,
            depends_on=[],
            resource=Resource.HANDS,
        ),
        RecipeStep(
            step_id=FT_B_COOK,
            description="Sauté on stovetop for 1 hour",
            duration_minutes=60,
            depends_on=[FT_B_PREP],
            resource=Resource.STOVETOP,
        ),
    ],
)

ENRICHED_FT_RECIPE_C = EnrichedRecipe(
    source=RAW_FT_RECIPE_C,
    steps=[
        RecipeStep(
            step_id=FT_C_PREP,
            description="Prepare ingredients for medium roast",
            duration_minutes=20,
            depends_on=[],
            resource=Resource.HANDS,
        ),
        RecipeStep(
            step_id=FT_C_COOK,
            description="Roast in oven for 1 hour",
            duration_minutes=60,
            depends_on=[FT_C_PREP],
            resource=Resource.OVEN,
        ),
    ],
)


# ── Authored recipe fixtures ─────────────────────────────────────────────────

AUTHORED_BRAISED_CHICKEN = AuthoredRecipeCreate(
    user_id=uuid.UUID("00000000-0000-0000-0000-000000000111"),
    title="Braised Chicken with Saffron Onions",
    description="A chef-authored braise with make-ahead holding and precise reheating guidance.",
    cuisine="Modern French",
    yield_info=AuthoredRecipeYield(quantity=4, unit="plates", notes="One leg quarter plus sauce per plate"),
    ingredients=[
        Ingredient(name="chicken leg quarters", quantity="4", preparation="patted dry"),
        Ingredient(name="yellow onions", quantity="3 large", preparation="thinly sliced"),
        Ingredient(name="chicken stock", quantity="750ml"),
        Ingredient(name="saffron", quantity="1 pinch", preparation="bloomed in warm stock"),
    ],
    steps=[
        AuthoredRecipeStep(
            title="Brown the chicken",
            instruction="Sear skin-side down until mahogany, then flip briefly to kiss the flesh side.",
            duration_minutes=12,
            duration_max=15,
            resource=Resource.STOVETOP,
            required_equipment=["braiser"],
            target_internal_temperature_f=155,
            chef_notes="Do not move the chicken early or the skin will tear.",
        ),
        AuthoredRecipeStep(
            title="Build the onion base",
            instruction="Sweat the onions with salt until collapsed, then add saffron stock and reduce slightly.",
            duration_minutes=15,
            resource=Resource.HANDS,
            required_equipment=["braiser"],
            dependencies=[
                AuthoredRecipeDependency(
                    step_id=build_authored_step_id("Braised Chicken with Saffron Onions", 1)
                )
            ],
            yield_contribution="Forms the braising liquor and onion garnish.",
        ),
        AuthoredRecipeStep(
            title="Braise until tender",
            instruction="Return chicken to the pan, cover, and cook gently until the joints loosen.",
            duration_minutes=45,
            duration_max=60,
            resource=Resource.OVEN,
            required_equipment=["braiser", "oven"],
            dependencies=[
                AuthoredRecipeDependency(
                    step_id=build_authored_step_id("Braised Chicken with Saffron Onions", 2)
                )
            ],
            can_be_done_ahead=True,
            prep_ahead_window="up to 2 days ahead",
            prep_ahead_notes="Cool in the liquid, then refrigerate covered.",
            until_condition="The leg joint yields easily when nudged.",
        ),
    ],
    equipment_notes=["Heavy braiser required for even heat retention", "Warm plates before saucing"],
    storage=AuthoredRecipeStorageGuidance(
        method="Refrigerated in braising liquid",
        duration="up to 2 days",
        notes="Keep onions submerged to prevent drying.",
    ),
    hold=AuthoredRecipeHoldGuidance(
        method="Warm hold at 140F",
        max_duration="20 minutes",
        notes="Vent lid slightly so the skin does not soften completely.",
    ),
    reheat=AuthoredRecipeReheatGuidance(
        method="Covered oven reheat",
        target="165F in the thickest part",
        notes="Baste once halfway through reheating.",
    ),
    make_ahead_guidance="Complete the braise the day before, chill overnight, and reheat in the liquid.",
    plating_notes="Spoon onions first, then set the glazed chicken on top.",
    chef_notes="The sauce should coat the back of a spoon without looking syrupy.",
)
