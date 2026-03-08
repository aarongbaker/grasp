"""
tests/fixtures/schedules.py
Hand-resolved schedules — the KNOWN-CORRECT ANSWERS.

Phase 6 validates real DAG merger output AGAINST these fixtures.
The scheduled timings must match or improve on what's defined here.

Scheduling logic applied manually:
  T+0:   Sear short ribs (STOVETOP, 20 min)
  T+20:  Make braising liquid (HANDS, 10 min)
  T+30:  Braise in oven (OVEN/PASSIVE, 150-180 min) ← PASSIVE allows parallelism
  T+30:  Melt chocolate + butter (STOVETOP, 10 min) ← during braise
  T+40:  Make fondant batter (HANDS, 15 min)
  T+55:  Butter ramekins, fill (HANDS, 10 min)
  T+65:  Chill fondants (PASSIVE, 30 min) ← can overlap
  T+65:  Boil potatoes (STOVETOP, 30 min) ← during chill
  T+95:  Rice and dry potatoes (HANDS, 10 min)
  T+105: Finish puree (HANDS, 15 min)
  T+180: [Braise window ends, adjust based on oven]
  T+180: Bake fondants (OVEN, 12-14 min)
  T+192: Braise done, rest ribs (PASSIVE, 15 min)
  Total: ~207 minutes

PASSIVE parallelism key: SR_STEP_3 (braise) runs from T+30 to T+180.
During this window: fondant batter, ramekin prep, chill, potato boil,
puree finishing — all can run concurrently. This is the primary source of
time savings vs sequential execution (~330 min sequential, ~207 min parallel).

RecipeDAG edges:
  Short Rib: step_1 → step_2 → step_3 → step_4 (sequential)
  Pommes Puree: step_1 → step_2 → step_3 (sequential)
  Fondant: step_1 → step_2 → step_3 → step_4 → step_5 (sequential within recipe)

Cross-recipe parallelism is enforced by the merger's resource contention rules,
not by explicit cross-recipe depends_on edges.
"""

from models.scheduling import (
    RecipeDAG, MergedDAG, ScheduledStep,
    NaturalLanguageSchedule, TimelineEntry,
)
from models.enums import Resource
from tests.fixtures.recipes import (
    SR_STEP_1, SR_STEP_2, SR_STEP_3, SR_STEP_4,
    PP_STEP_1, PP_STEP_2, PP_STEP_3,
    CF_STEP_1, CF_STEP_2, CF_STEP_3, CF_STEP_4, CF_STEP_5,
)


# ── Recipe DAGs ───────────────────────────────────────────────────────────────

RECIPE_DAG_SHORT_RIBS = RecipeDAG(
    recipe_name="Braised Short Ribs",
    recipe_slug="short_rib",
    steps=[],          # Steps live in EnrichedRecipe; DAG stores edges only
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
    recipe_slug="fondant",
    steps=[],
    edges=[
        (CF_STEP_1, CF_STEP_2),
        (CF_STEP_2, CF_STEP_3),
        (CF_STEP_3, CF_STEP_4),
        (CF_STEP_4, CF_STEP_5),
    ],
)


# ── Scheduled Steps (hand-resolved absolute timings) ─────────────────────────

_SCHEDULED_STEPS_FULL = [
    ScheduledStep(step_id=SR_STEP_1, recipe_name="Braised Short Ribs",
        description="Season and sear short ribs on all sides until deeply browned.",
        resource=Resource.STOVETOP, duration_minutes=20,
        start_at_minute=0, end_at_minute=20, depends_on=[]),

    ScheduledStep(step_id=SR_STEP_2, recipe_name="Braised Short Ribs",
        description="Sweat aromatics, deglaze with wine, add stock and herbs.",
        resource=Resource.HANDS, duration_minutes=10,
        start_at_minute=20, end_at_minute=30, depends_on=[SR_STEP_1]),

    ScheduledStep(step_id=SR_STEP_3, recipe_name="Braised Short Ribs",
        description="Braise in 150°C oven, covered, for 2.5-3 hours.",
        resource=Resource.OVEN, duration_minutes=150, duration_max=180,
        start_at_minute=30, end_at_minute=180,
        can_be_done_ahead=True, prep_ahead_window="up to 2 days in advance",
        prep_ahead_notes="Cool in braising liquid. Reheat gently with strained jus.",
        depends_on=[SR_STEP_2]),

    # Fondant runs during the braise (PASSIVE window)
    ScheduledStep(step_id=CF_STEP_1, recipe_name="Chocolate Fondant",
        description="Melt chocolate and butter together over a bain-marie.",
        resource=Resource.STOVETOP, duration_minutes=10,
        start_at_minute=30, end_at_minute=40, depends_on=[]),

    ScheduledStep(step_id=CF_STEP_2, recipe_name="Chocolate Fondant",
        description="Whisk eggs, yolks, sugar until pale. Fold in chocolate, then flour.",
        resource=Resource.HANDS, duration_minutes=15,
        start_at_minute=40, end_at_minute=55, depends_on=[CF_STEP_1]),

    ScheduledStep(step_id=CF_STEP_3, recipe_name="Chocolate Fondant",
        description="Butter and cocoa-dust ramekins. Fill to within 5mm of rim.",
        resource=Resource.HANDS, duration_minutes=10,
        start_at_minute=55, end_at_minute=65, depends_on=[CF_STEP_2]),

    ScheduledStep(step_id=CF_STEP_4, recipe_name="Chocolate Fondant",
        description="Refrigerate filled ramekins for at least 30 minutes.",
        resource=Resource.PASSIVE, duration_minutes=30,
        start_at_minute=65, end_at_minute=95,
        can_be_done_ahead=True, prep_ahead_window="up to 24 hours in advance",
        prep_ahead_notes="Bake straight from fridge for clean molten centre.",
        depends_on=[CF_STEP_3]),

    # Potatoes start during fondant chill (both PASSIVE/STOVETOP — no conflict)
    ScheduledStep(step_id=PP_STEP_1, recipe_name="Pommes Puree",
        description="Boil potatoes in well-salted water from cold until tender.",
        resource=Resource.STOVETOP, duration_minutes=30,
        start_at_minute=65, end_at_minute=95, depends_on=[]),

    ScheduledStep(step_id=PP_STEP_2, recipe_name="Pommes Puree",
        description="Drain and rice potatoes while hot. Steam-dry in pot.",
        resource=Resource.HANDS, duration_minutes=10,
        start_at_minute=95, end_at_minute=105, depends_on=[PP_STEP_1]),

    ScheduledStep(step_id=PP_STEP_3, recipe_name="Pommes Puree",
        description="Beat in cold butter cube by cube. Stream warm milk. Season. Sieve.",
        resource=Resource.HANDS, duration_minutes=15,
        start_at_minute=105, end_at_minute=120, depends_on=[PP_STEP_2]),

    # Fondant bake near service (after braise window)
    ScheduledStep(step_id=CF_STEP_5, recipe_name="Chocolate Fondant",
        description="Bake at 200°C for 12-14 min. Edges set, centre wobbles. Serve immediately.",
        resource=Resource.OVEN, duration_minutes=12, duration_max=14,
        start_at_minute=180, end_at_minute=192, depends_on=[CF_STEP_4]),

    ScheduledStep(step_id=SR_STEP_4, recipe_name="Braised Short Ribs",
        description="Rest ribs, loosely tented with foil.",
        resource=Resource.PASSIVE, duration_minutes=15,
        start_at_minute=180, end_at_minute=195, depends_on=[SR_STEP_3]),
]

MERGED_DAG_FULL = MergedDAG(
    scheduled_steps=_SCHEDULED_STEPS_FULL,
    total_duration_minutes=207,
)

# Two-recipe version (no fondant — used in recoverable_error test)
_SCHEDULED_STEPS_TWO = [s for s in _SCHEDULED_STEPS_FULL
                         if not s.step_id.startswith("fondant_")]

MERGED_DAG_TWO_RECIPE = MergedDAG(
    scheduled_steps=_SCHEDULED_STEPS_TWO,
    total_duration_minutes=195,
)


# ── Natural Language Schedules ────────────────────────────────────────────────

def _make_timeline_entry(step: ScheduledStep) -> TimelineEntry:
    heads_up = None
    if step.duration_max and step.duration_max != step.duration_minutes:
        heads_up = f"{step.duration_minutes}–{step.duration_max} min depending on oven"

    return TimelineEntry(
        time_offset_minutes=step.start_at_minute,
        label=f"T+{step.start_at_minute}",
        step_id=step.step_id,
        recipe_name=step.recipe_name,
        action=step.description,
        resource=step.resource,
        duration_minutes=step.duration_minutes,
        duration_max=step.duration_max,
        heads_up=heads_up,
        is_prep_ahead=step.can_be_done_ahead,
        prep_ahead_window=step.prep_ahead_window,
    )


NATURAL_LANGUAGE_SCHEDULE_FULL = NaturalLanguageSchedule(
    timeline=[_make_timeline_entry(s) for s in _SCHEDULED_STEPS_FULL],
    total_duration_minutes=207,
    summary=(
        "A three-course dinner party menu for 4 guests. "
        "Short rib braise (3 hours, oven) anchors the schedule — "
        "fondant batter and pommes puree prep run concurrently during the braise window. "
        "Fondants bake in 12–14 minutes at service while ribs rest. "
        "Total active time: approximately 90 minutes. Total elapsed: 3 hours 27 minutes."
    ),
)

NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE = NaturalLanguageSchedule(
    timeline=[_make_timeline_entry(s) for s in _SCHEDULED_STEPS_TWO],
    total_duration_minutes=195,
    summary=(
        "A two-course dinner for 4 guests (fondant omitted due to RAG retrieval failure). "
        "Short rib braise anchors the schedule; pommes puree finishes during the braise window. "
        "Total elapsed: 3 hours 15 minutes."
    ),
    error_summary="Chocolate Fondant dropped: RAG retrieval returned zero results.",
)
