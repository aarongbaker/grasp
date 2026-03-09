"""
graph/graph.py
The compiled LangGraph state machine. Built once in Phase 3.
Graph topology is locked here — never revisited in Phases 4-7.
Each later phase only swaps one import line (mock → real node).

Graph topology (linear with conditional error routing):
  START → recipe_generator → [error_router] → fatal? → handle_fatal_error → END
                                             → continue → rag_enricher
        → [error_router] → ... → dag_merger → [error_router]
        → schedule_renderer → [final_router] → mark_complete → END
                                              → mark_partial  → END

CRITICAL import pattern for Phases 4-7:
  Phase 4: from graph.nodes.generator import recipe_generator_node  (delete mock)
  Phase 5: from graph.nodes.enricher import rag_enricher_node       (delete mock)
  etc.

The three lines after "# ── Node Imports (swap here in Phases 4-7) ──" are
the ONLY lines that change across all subsequent phases.
"""

from langgraph.graph import StateGraph, END
from models.pipeline import GRASPState
from graph.router import error_router, final_router

# ── Node Imports (swap here in Phases 4-7) ───────────────────────────────────
from graph.nodes.generator import recipe_generator_node         # Phase 4: real
from graph.nodes.mock_enricher import rag_enricher_node         # Phase 5: swap
from graph.nodes.mock_validator import validator_node            # Phase 5: swap (real Pydantic)
from graph.nodes.mock_dag_builder import dag_builder_node       # Phase 6: swap
from graph.nodes.mock_dag_merger import dag_merger_node         # Phase 6: swap
from graph.nodes.mock_renderer import schedule_renderer_node    # Phase 7: swap
# ─────────────────────────────────────────────────────────────────────────────


async def handle_fatal_error_node(state: GRASPState) -> dict:
    """
    Terminal node for unrecoverable failures.
    The errors list already contains the fatal NodeError that triggered this.
    Returns empty errors list — operator.add makes this a no-op.
    """
    return {"errors": []}


async def mark_complete_node(state: GRASPState) -> dict:
    """Terminal node — pipeline completed with no errors."""
    return {"errors": []}


async def mark_partial_node(state: GRASPState) -> dict:
    """Terminal node — pipeline completed with recoverable errors."""
    return {"errors": []}


def build_grasp_graph(checkpointer) -> StateGraph:
    """
    Compiles and returns the LangGraph state machine.
    checkpointer: AsyncPostgresSaver instance (production) or
                  MemorySaver instance (unit tests without Postgres).

    Called once at application startup in main.py lifespan hook.
    The compiled graph is stored as an app state variable and injected
    into Celery tasks and routes via the application instance.
    """
    workflow = StateGraph(GRASPState)

    # ── Add nodes ─────────────────────────────────────────────────────────────
    workflow.add_node("recipe_generator",  recipe_generator_node)
    workflow.add_node("rag_enricher",      rag_enricher_node)
    workflow.add_node("validator",         validator_node)
    workflow.add_node("dag_builder",       dag_builder_node)
    workflow.add_node("dag_merger",        dag_merger_node)
    workflow.add_node("schedule_renderer", schedule_renderer_node)
    workflow.add_node("handle_fatal_error", handle_fatal_error_node)
    workflow.add_node("mark_complete",     mark_complete_node)
    workflow.add_node("mark_partial",      mark_partial_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    workflow.set_entry_point("recipe_generator")

    # ── Conditional edges after each non-terminal node ────────────────────────
    # error_router runs after every node except schedule_renderer.
    # The mapping: "fatal" → handle_fatal_error, "continue" → next_node
    workflow.add_conditional_edges(
        "recipe_generator",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "rag_enricher"},
    )
    workflow.add_conditional_edges(
        "rag_enricher",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "validator"},
    )
    workflow.add_conditional_edges(
        "validator",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "dag_builder"},
    )
    workflow.add_conditional_edges(
        "dag_builder",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "dag_merger"},
    )
    workflow.add_conditional_edges(
        "dag_merger",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "schedule_renderer"},
    )

    # ── Final router after schedule_renderer ─────────────────────────────────
    workflow.add_conditional_edges(
        "schedule_renderer",
        final_router,
        {"complete": "mark_complete", "partial": "mark_partial"},
    )

    # ── Terminal edges ────────────────────────────────────────────────────────
    workflow.add_edge("handle_fatal_error", END)
    workflow.add_edge("mark_complete", END)
    workflow.add_edge("mark_partial", END)

    return workflow.compile(checkpointer=checkpointer)
