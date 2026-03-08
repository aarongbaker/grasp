"""
graph/nodes/mock_renderer.py
Mock schedule renderer. Returns the fixture NaturalLanguageSchedule.
Deleted in Phase 7.

Renderer failure is RECOVERABLE (recoverable=True). A partial schedule
is always better than no schedule. This is unlike generator (always fatal)
and dag_merger (always fatal on RESOURCE_CONFLICT).

The mock always succeeds — renderer failure testing can be done by
directly constructing a partial state in a future test.

summary is stored in Session.schedule_summary by finalise_session()
for the session list view. The mock summary should be realistic.
"""

from models.pipeline import GRASPState
from tests.fixtures.schedules import NATURAL_LANGUAGE_SCHEDULE_FULL, NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE


async def schedule_renderer_node(state: GRASPState) -> dict:
    # Return appropriate schedule based on how many recipes made it through
    merged_dag = state.get("merged_dag")
    errors = state.get("errors", [])

    if errors:
        # Partial path — some recipes dropped. Use two-recipe schedule.
        schedule = NATURAL_LANGUAGE_SCHEDULE_TWO_RECIPE
    else:
        schedule = NATURAL_LANGUAGE_SCHEDULE_FULL

    return {"schedule": schedule.model_dump()}
