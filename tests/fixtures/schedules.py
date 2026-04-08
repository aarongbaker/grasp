"""
tests/fixtures/schedules.py
Algorithm-verified schedules — the KNOWN-CORRECT ANSWERS.

Phase 6 validates real DAG merger output AGAINST these fixtures.
The scheduled timings are the deterministic output of the greedy
list-scheduling algorithm with critical-path priority.

Resource model (V1 — pure resource pools, all independent):
  HANDS:    capacity = 1 (exclusive)
  STOVETOP: capacity = max_burners = 4 (multi-burner)
  OVEN:     capacity = 1 per oven (has_second_oven=False → 1)
  PASSIVE:  unlimited

Scheduling priority: (-critical_path_length, recipe_slug, step_id)
Output sort order: (start_at_minute, recipe_name, step_id)

3-Recipe schedule (multi-burner: 3 STOVETOP steps at T+0):
  T+0:   Sear ribs (STOVETOP, 20)          ← short_rib, cp=195
  T+0:   Melt chocolate (STOVETOP, 10)     ← fondant, cp=77
  T+0:   Boil potatoes (STOVETOP, 30)      ← pommes, cp=55
  T+20:  Braising liquid (HANDS, 10)
  T+30:  Braise (OVEN, 150)
  T+30:  Fondant batter (HANDS, 15)        ← HANDS free after SR2
  T+45:  Fill ramekins (HANDS, 10)
  T+55:  Chill fondants (PASSIVE, 30)
  T+55:  Rice potatoes (HANDS, 10)         ← during chill
  T+65:  Finish puree (HANDS, 15)
  T+180: Rest ribs (PASSIVE, 15)           ← braise done
  T+180: Bake fondants (OVEN, 12)
  Total: 195 minutes

RecipeDAG edges:
  Short Rib: step_1 → step_2 → step_3 → step_4 (sequential)
  Pommes Puree: step_1 → step_2 → step_3 (sequential)
  Fondant: step_1 → step_2 → step_3 → step_4 → step_5 (sequential within recipe)

Cross-recipe parallelism is enforced by the merger's resource contention rules,
not by explicit cross-recipe depends_on edges.
"""

from app.models.enums import Resource
from app.models.scheduling import (
    MergedDAG,
    NaturalLanguageSchedule,
    OneOvenConflictRemediation,
    OneOvenConflictSummary,
    RecipeDAG,
    ScheduledStep,
    TimelineEntry,
)
from tests.fixtures.recipes import (
    CF_STEP_1,
    CF_STEP_2,
    CF_STEP_3,
    CF_STEP_4,
    CF_STEP_5,
    PP_STEP_1,
    PP_STEP_2,
    PP_STEP_3,
    SR_STEP_1,
    SR_STEP_2,
    SR_STEP_3,
    SR_STEP_4,
)


def _is_meaningful_prep_ahead(step: ScheduledStep) -> bool:
    """Mirror renderer time-gate: prep-ahead only when window contains 'hour', 'day', or 'week'."""
    if not step.can_be_done_ahead:
        return False
    if not step.prep_ahead_window:
        return False
    window = step.prep_ahead_window.lower()
    return "hour" in window or "day" in window or "week" in window


_RESOURCE_HEADS_UP: dict[Resource, str] = {
    Resource.OVEN: "oven temperature and size",
    Resource.STOVETOP: "stovetop heat",
    Resource.HANDS: "timing",
    Resource.PASSIVE: "conditions",
}

# ── Recipe DAGs ───────────────────────────────────────────────────────────────
# Slugs match _generate_recipe_slug() output from graph/nodes/dag_builder.py

RECIPE_DAG_SHORT_RIBS = RecipeDAG(
    recipe_name="Braised Short Ribs",
    recipe_slug="braised_short_ribs",
    steps=[],  # Steps live in EnrichedRecipe; DAG stores edges only
    edges=[
        (SR_STEP_1, SR_STEP_2),
        (SR_STEP_2, SR_STEP_3),
        (SR_STEP_3, SR_STEP_4),
    ],
)

RECIPE_DAG_POMMES_PUREE = RecipeDAG(
    recipe_name="Pommes Puree",
    recipe_slug="pommes_puree",
    steps=[],
    edges=[
        (PP_STEP_1, PP_STEP_2),
        (PP_STEP_2, PP_STEP_3),
    ],
)

RECIPE_DAG_FONDANT = RecipeDAG(
    recipe_name="Chocolate Fondant",
    recipe_slug="chocolate_fondant",
    steps=[],
    edges=[
        (CF_STEP_1, CF_STEP_2),
        (CF_STEP_2, CF_STEP_3),
        (CF_STEP_3, CF_STEP_4),
        (CF_STEP_4, CF_STEP_5),
    ],
)


# ── Scheduled Steps: 3-recipe (algorithm-verified absolute timings) ──────────
# Sort order: (start_at_minute, recipe_name, step_id)
# "Braised Short Ribs" < "Chocolate Fondant" < "Pommes Puree"

_SCHEDULED_STEPS_FULL = [
    # T+0: Three STOVETOP steps in parallel (multi-burner, capacity=4)
    ScheduledStep(
        step_id=SR_STEP_1,
        recipe_name="Braised Short Ribs",
        description="Season and sear short ribs on all sides until deeply browned.",
        resource=Resource.STOVETOP,
        duration_minutes=20,
        start_at_minute=0,
        end_at_minute=20,
        depends_on=[],
    ),
    ScheduledStep(
        step_id=CF_STEP_1,
        recipe_name="Chocolate Fondant",
        description="Melt chocolate and butter together over a bain-marie.",
        resource=Resource.STOVETOP,
        duration_minutes=10,
        start_at_minute=0,
        end_at_minute=10,
        depends_on=[],
    ),
    ScheduledStep(
        step_id=PP_STEP_1,
        recipe_name="Pommes Puree",
        description="Boil potatoes in well-salted water from cold until tender.",
        resource=Resource.STOVETOP,
        duration_minutes=30,
        start_at_minute=0,
        end_at_minute=30,
        depends_on=[],
    ),
    # T+20: SR2 (HANDS) — first HANDS step, wins by cp=175
    ScheduledStep(
        step_id=SR_STEP_2,
        recipe_name="Braised Short Ribs",
        description="Sweat aromatics, deglaze with wine, add stock and herbs.",
        resource=Resource.HANDS,
        duration_minutes=10,
        start_at_minute=20,
        end_at_minute=30,
        depends_on=[SR_STEP_1],
    ),
    # T+30: SR3 (OVEN) + CF2 (HANDS, cp=67) — HANDS free after SR2
    ScheduledStep(
        step_id=SR_STEP_3,
        recipe_name="Braised Short Ribs",
        description="Braise in 150°C oven, covered, for 2.5-3 hours.",
        resource=Resource.OVEN,
        duration_minutes=150,
        duration_max=180,
        start_at_minute=30,
        end_at_minute=180,
        can_be_done_ahead=True,
        prep_ahead_window="up to 2 days in advance",
        prep_ahead_notes="Cool in braising liquid. Reheat gently with strained jus.",
        depends_on=[SR_STEP_2],
    ),
    ScheduledStep(
        step_id=CF_STEP_2,
        recipe_name="Chocolate Fondant",
        description="Whisk eggs, yolks, sugar until pale. Fold in chocolate, then flour.",
        resource=Resource.HANDS,
        duration_minutes=15,
        start_at_minute=30,
        end_at_minute=45,
        depends_on=[CF_STEP_1],
    ),
    # T+45: CF3 (HANDS) — fondant chain continues
    ScheduledStep(
        step_id=CF_STEP_3,
        recipe_name="Chocolate Fondant",
        description="Butter and cocoa-dust ramekins. Fill to within 5mm of rim.",
        resource=Resource.HANDS,
        duration_minutes=10,
        start_at_minute=45,
        end_at_minute=55,
        depends_on=[CF_STEP_2],
    ),
    # T+55: CF4 (PASSIVE) + PP2 (HANDS) — fondant chills, potatoes riced
    ScheduledStep(
        step_id=CF_STEP_4,
        recipe_name="Chocolate Fondant",
        description="Refrigerate filled ramekins for at least 30 minutes.",
        resource=Resource.PASSIVE,
        duration_minutes=30,
        start_at_minute=55,
        end_at_minute=85,
        can_be_done_ahead=True,
        prep_ahead_window="up to 24 hours in advance",
        prep_ahead_notes="Bake straight from fridge for clean molten centre.",
        depends_on=[CF_STEP_3],
    ),
    ScheduledStep(
        step_id=PP_STEP_2,
        recipe_name="Pommes Puree",
        description="Drain and rice potatoes while hot. Steam-dry in pot.",
        resource=Resource.HANDS,
        duration_minutes=10,
        start_at_minute=55,
        end_at_minute=65,
        depends_on=[PP_STEP_1],
    ),
    # T+65: PP3 (HANDS) — finish puree
    ScheduledStep(
        step_id=PP_STEP_3,
        recipe_name="Pommes Puree",
        description="Beat in cold butter cube by cube. Stream warm milk. Season. Sieve.",
        resource=Resource.HANDS,
        duration_minutes=15,
        start_at_minute=65,
        end_at_minute=80,
        depends_on=[PP_STEP_2],
    ),
    # T+180: SR4 (PASSIVE) + CF5 (OVEN) — braise window ends
    ScheduledStep(
        step_id=SR_STEP_4,
        recipe_name="Braised Short Ribs",
        description="Rest ribs, loosely tented with foil.",
        resource=Resource.PASSIVE,
        duration_minutes=15,
        start_at_minute=180,
        end_at_minute=195,
        depends_on=[SR_STEP_3],
    ),
    ScheduledStep(
        step_id=CF_STEP_5,
        recipe_name="Chocolate Fondant",
        description="Bake at 200°C for 12-14 min. Edges set, centre wobbles. Serve immediately.",
        resource=Resource.OVEN,
        duration_minutes=12,
        duration_max=14,
        start_at_minute=180,
        end_at_minute=192,
        depends_on=[CF_STEP_4],
    ),
]

MERGED_DAG_FULL = MergedDAG(
    scheduled_steps=_SCHEDULED_STEPS_FULL,
    total_duration_minutes=195,
    total_duration_minutes_max=210,
    active_time_minutes=282,
    resource_utilisation={
        "stovetop": [(0, 10), (0, 20), (0, 30)],
        "hands": [(20, 30), (30, 45), (45, 55), (55, 65), (65, 80)],
        "oven": [(30, 180), (180, 192)],
    },
    one_oven_conflict=OneOvenConflictSummary(
        classification="compatible",
        tolerance_f=15,
        has_second_oven=False,
    ),
)


# ── Scheduled Steps: 2-recipe (no fondant — independent computation) ─────────
# When fondant is dropped, pommes steps shift earlier (HANDS freed sooner).
# Sort order: (start_at_minute, recipe_name, step_id)
# "Braised Short Ribs" < "Pommes Puree"

_SCHEDULED_STEPS_TWO = [
    ScheduledStep(
        step_id=SR_STEP_1,
        recipe_name="Braised Short Ribs",
        description="Season and sear short ribs on all sides until deeply browned.",
        resource=Resource.STOVETOP,
        duration_minutes=20,
        start_at_minute=0,
        end_at_minute=20,
        depends_on=[],
    ),
    ScheduledStep(
        step_id=PP_STEP_1,
        recipe_name="Pommes Puree",
        description="Boil potatoes in well-salted water from cold until tender.",
        resource=Resource.STOVETOP,
        duration_minutes=30,
        start_at_minute=0,
        end_at_minute=30,
        depends_on=[],
    ),
    ScheduledStep(
        step_id=SR_STEP_2,
        recipe_name="Braised Short Ribs",
        description="Sweat aromatics, deglaze with wine, add stock and herbs.",
        resource=Resource.HANDS,
        duration_minutes=10,
        start_at_minute=20,
        end_at_minute=30,
        depends_on=[SR_STEP_1],
    ),
    ScheduledStep(
        step_id=SR_STEP_3,
        recipe_name="Braised Short Ribs",
        description="Braise in 150°C oven, covered, for 2.5-3 hours.",
        resource=Resource.OVEN,
        duration_minutes=150,
        duration_max=180,
        start_at_minute=30,
        end_at_minute=180,
        can_be_done_ahead=True,
        prep_ahead_window="up to 2 days in advance",
        prep_ahead_notes="Cool in braising liquid. Reheat gently with strained jus.",
        depends_on=[SR_STEP_2],
    ),
    ScheduledStep(
        step_id=PP_STEP_2,
        recipe_name="Pommes Puree",
        description="Drain and rice potatoes while hot. Steam-dry in pot.",
        resource=Resource.HANDS,
        duration_minutes=10,
        start_at_minute=30,
        end_at_minute=40,
        depends_on=[PP_STEP_1],
    ),
    ScheduledStep(
        step_id=PP_STEP_3,
        recipe_name="Pommes Puree",
        description="Beat in cold butter cube by cube. Stream warm milk. Season. Sieve.",
        resource=Resource.HANDS,
        duration_minutes=15,
        start_at_minute=40,
        end_at_minute=55,
        depends_on=[PP_STEP_2],
    ),
    ScheduledStep(
        step_id=SR_STEP_4,
        recipe_name="Braised Short Ribs",
        description="Rest ribs, loosely tented with foil.",
        resource=Resource.PASSIVE,
        duration_minutes=15,
        start_at_minute=180,
        end_at_minute=195,
        depends_on=[SR_STEP_3],
    ),
]

MERGED_DAG_TWO_RECIPE = MergedDAG(
    scheduled_steps=_SCHEDULED_STEPS_TWO,
    total_duration_minutes=195,
    total_duration_minutes_max=210,
    active_time_minutes=235,
    resource_utilisation={
        "stovetop": [(0, 20), (0, 30)],
        "hands": [(20, 30), (30, 40), (40, 55)],
        "oven": [(30, 180)],
    },
    one_oven_conflict=OneOvenConflictSummary(
        classification="compatible",
        tolerance_f=15,
        has_second_oven=False,
    ),
)


# ── Natural Language Schedules ────────────────────────────────────────────────


def _make_timeline_entry(step: ScheduledStep) -> TimelineEntry:
    heads_up = None
    buffer = None
    if step.duration_max and step.duration_max != step.duration_minutes:
        heads_up = f"{step.duration_minutes}–{step.duration_max} min depending on {_RESOURCE_HEADS_UP[step.resource]}"
        buffer = step.duration_max - step.duration_minutes

    return TimelineEntry(
        time_offset_minutes=step.start_at_minute,
        label=f"T+{step.start_at_minute}",
        step_id=step.step_id,
        recipe_name=step.recipe_name,
        action=step.description,
        resource=step.resource,
        duration_minutes=step.duration_minutes,
        duration_max=step.duration_max,
        buffer_minutes=buffer,
        heads_up=heads_up,
        is_prep_ahead=_is_meaningful_prep_ahead(step),
        prep_ahead_window=step.prep_ahead_window,
    )


def _build_unified_timeline(steps: list[ScheduledStep]) -> list[TimelineEntry]:
    """Build unified timeline matching renderer logic — all steps in a single list."""
    return [_make_timeline_entry(step) for step in steps]


_TIMELINE_FULL = _build_unified_timeline(_SCHEDULED_STEPS_FULL)

NATURAL_LANGUAGE_SCHEDULE_FULL = NaturalLanguageSchedule(
    timeline=_TIMELINE_FULL,
    prep_ahead_entries=[],
    total_duration_minutes=195,
    total_duration_minutes_max=210,
    active_time_minutes=282,
    summary=(
        "A three-course dinner party menu for 4 guests. "
        "Short rib braise (3 hours, oven) anchors the schedule — "
        "fondant batter and pommes puree prep run concurrently during the braise window. "
        "Fondants bake in 12–14 minutes at service while ribs rest. "
        "Total active time: approximately 90 minutes. Total elapsed: 3 hours 15 minutes."
    ),
    one_oven_conflict=OneOvenConflictSummary(
        classification="compatible",
        tolerance_f=15,
        has_second_oven=False,
    ),
)

_TIMELINE_TWO = _build_unified_timeline(_SCHEDULED_STEPS_TWO)

NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE = NaturalLanguageSchedule(
    timeline=_TIMELINE_TWO,
    prep_ahead_entries=[],
    total_duration_minutes=195,
    total_duration_minutes_max=210,
    active_time_minutes=235,
    summary=(
        "A two-course dinner for 4 guests (fondant omitted due to RAG retrieval failure). "
        "Short rib braise anchors the schedule; pommes puree finishes during the braise window. "
        "Total elapsed: 3 hours 15 minutes."
    ),
    error_summary="Chocolate Fondant dropped: RAG retrieval returned zero results.",
    one_oven_conflict=OneOvenConflictSummary(
        classification="compatible",
        tolerance_f=15,
        has_second_oven=False,
    ),
)


# ── Finish-Together Test Fixtures ─────────────────────────────────────────────
# Three recipes with different cooking durations to test finish-together scheduling.
#
# Recipe A (Long Braise):
#   - 30 min prep (HANDS)
#   - 180 min cook (OVEN) = 3 hours cooking
#   Total cooking: 180 min (anchor)
#
# Recipe B (Quick Sauté):
#   - 15 min prep (HANDS)
#   - 60 min cook (STOVETOP) = 1 hour cooking
#   Total cooking: 60 min → offset = 120 min
#
# Recipe C (Medium Roast):
#   - 20 min prep (HANDS)
#   - 60 min cook (OVEN) = 1 hour cooking
#   Total cooking: 60 min → offset = 120 min
#
# With finish-together scheduling:
#   - Recipe A cooking starts at T+30 (after prep), ends at T+210
#   - Recipe B cooking starts at T+120 (offset) + prep done → max(15, 120) = 120, ends at T+180
#   - Recipe C cooking starts at T+120 (offset) + prep done → max(20, 120) = 120, ends at T+180
#   - All cooking finishes within 30 min window (180-210)
#
# With ASAP scheduling (no serving_time):
#   - Recipe A cooking starts at T+30, ends at T+210
#   - Recipe B cooking starts at T+15, ends at T+75
#   - Recipe C cooking starts at T+20, ends at T+80
#   - Cooking finish times span 135 min (75 to 210)

# Step IDs for finish-together test fixtures
FT_A_PREP = "ft_recipe_a_prep"
FT_A_COOK = "ft_recipe_a_cook"
FT_B_PREP = "ft_recipe_b_prep"
FT_B_COOK = "ft_recipe_b_cook"
FT_C_PREP = "ft_recipe_c_prep"
FT_C_COOK = "ft_recipe_c_cook"


# RecipeDAG definitions for finish-together tests
RECIPE_DAG_FT_A = RecipeDAG(
    recipe_name="Recipe A Long Braise",
    recipe_slug="recipe_a_long_braise",
    steps=[],
    edges=[(FT_A_PREP, FT_A_COOK)],
)

RECIPE_DAG_FT_B = RecipeDAG(
    recipe_name="Recipe B Quick Saute",
    recipe_slug="recipe_b_quick_saute",
    steps=[],
    edges=[(FT_B_PREP, FT_B_COOK)],
)

RECIPE_DAG_FT_C = RecipeDAG(
    recipe_name="Recipe C Medium Roast",
    recipe_slug="recipe_c_medium_roast",
    steps=[],
    edges=[(FT_C_PREP, FT_C_COOK)],
)
