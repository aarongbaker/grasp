"""
graph/graph.py
The compiled LangGraph state machine. Built once in Phase 3.
Graph topology is locked here — never revisited in Phases 4-7.
Each later phase only swaps one import line (mock → real node).

Graph topology (linear with conditional error routing):
  START → recipe_generator → [error_router] → fatal? → handle_fatal_error → END
                                             → continue → enricher
        → [error_router] → ... → dag_merger → [dag_merger_router]
        → retry_generation? → recipe_generator
        → continue → schedule_renderer → [final_router] → mark_complete → END
                                                           → mark_partial  → END

CRITICAL import pattern for Phases 4-7:
  Phase 4: from app.graph.nodes.generator import recipe_generator_node  (delete mock)
  Phase 5: from app.graph.nodes.enricher import enrich_recipe_steps_node  (delete mock)
  etc.

The three lines after "# ── Node Imports (swap here in Phases 4-7) ──" are
the ONLY lines that change across all subsequent phases.
"""

# LangGraph's StateGraph is the core orchestration primitive. END is a sentinel
# node name that signals LangGraph to stop execution and flush the checkpoint.
from langgraph.graph import END, StateGraph

# These imports represent the finalized, real implementations for all pipeline
# stages. Each was originally a mock in Phase 3 and was swapped in during its
# respective phase. The comment on each line marks WHICH phase made the swap —
# this makes git blame immediately informative when debugging phase regressions.
from app.graph.nodes.dag_builder import dag_builder_node  # Phase 6: real
from app.graph.nodes.dag_merger import dag_merger_node  # Phase 6: real
from app.graph.nodes.enricher import enrich_recipe_steps_node  # Phase 5: real

# ── Node Imports (swap here in Phases 4-7) ───────────────────────────────────
# This comment block is intentional scaffolding left in place. The three lines
# below are the documented swap point for future phases. Even though all phases
# are now complete, keeping this marker makes the Phase 3 "topology lock"
# contract visible to any developer reading the file for the first time.
from app.graph.nodes.generator import recipe_generator_node  # Phase 4: real
from app.graph.nodes.renderer import schedule_renderer_node  # Phase 7: real
from app.graph.nodes.validator import validator_node  # Phase 5: real (Pydantic)

# Router functions live in a dedicated module so they can be unit-tested in
# isolation, imported by the test suite, and reasoned about without loading
# the full graph compilation machinery.
from app.graph.router import (
    build_generation_retry_state,  # builds the state patch that resets downstream fields
    dag_merger_router,             # 3-way router: fatal / continue / retry_generation
    error_router,                  # 2-way router: fatal / continue — fires after every node
    final_router,                  # 2-way router: complete / partial — fires only after renderer
)

# GRASPState is the shared TypedDict that flows through every node. Using
# TypedDict (not Pydantic) is intentional: LangGraph's checkpointer serializes
# and deserializes state as plain dicts, and TypedDict avoids the round-trip
# cost of Pydantic model instantiation on every checkpoint read.
from app.models.pipeline import GRASPState

# ─────────────────────────────────────────────────────────────────────────────


async def handle_fatal_error_node(state: GRASPState) -> dict:
    """
    Terminal node for unrecoverable failures.
    The errors list already contains the fatal NodeError that triggered this.
    Returns empty errors list — operator.add makes this a no-op.
    """
    # This node intentionally does nothing except exist as a named graph node.
    # Its only purpose is to give the graph a labelled stopping point for fatal
    # errors so the status in the checkpoint is "handle_fatal_error" rather than
    # the name of whichever pipeline node failed — that distinction matters when
    # the Celery worker reads final graph state to decide what to write back to
    # the session row in Postgres.
    #
    # Returning {"errors": []} is safe because GRASPState's errors field uses
    # operator.add as its LangGraph reducer, meaning returned lists are
    # *appended*, not overwritten. An empty list is a no-op append.
    return {"errors": []}


async def mark_complete_node(state: GRASPState) -> dict:
    """Terminal node — pipeline completed with no errors."""
    # Same no-op pattern as handle_fatal_error_node. The node's identity
    # (its name in the graph) is the signal; the return value is irrelevant.
    # final_router only routes here when state.errors is empty, so there is
    # nothing meaningful to write. The Celery worker distinguishes
    # "mark_complete" from "mark_partial" by reading the last node name from
    # the checkpoint metadata.
    return {"errors": []}


async def mark_partial_node(state: GRASPState) -> dict:
    """Terminal node — pipeline completed with recoverable errors."""
    # Reached when the schedule was produced but at least one recoverable error
    # was accumulated during enrichment or validation. The caller (Celery task / API route)
    # warnings to the user; this node itself has nothing to add.
    return {"errors": []}


async def retry_generation_node(state: GRASPState) -> dict:
    """Checkpoint-local corrective retry seam for eligible dag_merger failures."""
    # This node is the bridge between the dag_merger_router's "retry_generation"
    # branch and the back-edge to recipe_generator. It delegates all state-
    # mutation logic to build_generation_retry_state (in router.py) so that
    # the retry policy and the graph topology remain independently testable.
    #
    # Critically, the back-edge "retry_generation → recipe_generator" in the
    # graph means LangGraph will checkpoint here before looping. If the process
    # crashes mid-retry, the graph can resume from this node rather than
    # re-running the entire pipeline from scratch.
    return build_generation_retry_state(state)


def build_grasp_graph(checkpointer) -> StateGraph:
    """
    Compiles and returns the LangGraph state machine.
    checkpointer: AsyncPostgresSaver instance (production) or
                  MemorySaver instance (unit tests without Postgres).

    Called once at application startup in main.py lifespan hook.
    The compiled graph is stored as an app state variable and injected
    into Celery tasks and routes via the application instance.
    """
    # StateGraph is parameterized with GRASPState so LangGraph knows the
    # shape of the shared state dict and can apply the per-field reducers
    # (e.g. operator.add on errors) on every node return.
    workflow = StateGraph(GRASPState)

    # ── Add nodes ─────────────────────────────────────────────────────────────
    # All nodes are registered by string name. The string name is what appears
    # in checkpoint metadata and what conditional-edge maps use as keys.
    # Node order here is cosmetic — execution order is determined solely by
    # the edges added below.
    workflow.add_node("recipe_generator", recipe_generator_node)
    workflow.add_node("enricher", enrich_recipe_steps_node)       # LLM-only step enrichment
    workflow.add_node("validator", validator_node)              # Pydantic validation pass
    workflow.add_node("dag_builder", dag_builder_node)          # NetworkX DAG construction + cycle detection
    workflow.add_node("dag_merger", dag_merger_node)            # Greedy list scheduler (resource-aware)
    workflow.add_node("schedule_renderer", schedule_renderer_node)  # Deterministic timeline + Claude summary
    workflow.add_node("handle_fatal_error", handle_fatal_error_node)  # Terminal: unrecoverable failure
    workflow.add_node("mark_complete", mark_complete_node)     # Terminal: all errors resolved
    workflow.add_node("mark_partial", mark_partial_node)       # Terminal: schedule produced with warnings
    workflow.add_node("retry_generation", retry_generation_node)  # Loop back: oven-conflict auto-repair

    # ── Entry point ───────────────────────────────────────────────────────────
    # recipe_generator is always the first node. It receives the initial
    # DinnerConcept from state (set by the API route before graph invocation)
    # and emits raw_recipes into state for the enricher.
    workflow.set_entry_point("recipe_generator")

    # ── Conditional edges after each non-terminal node ────────────────────────
    # Every non-terminal pipeline node is followed by error_router. This is the
    # "check after every step" contract established in Phase 3. The router only
    # has two exits — "fatal" or "continue" — keeping the branching logic
    # uniform and easy to reason about.
    #
    # The dict passed to add_conditional_edges maps router return values to
    # node names. LangGraph validates this dict at compile time, so a typo in
    # a node name surfaces immediately rather than at runtime.

    # After recipe_generator: on fatal, halt. On continue, enrich.
    workflow.add_conditional_edges(
        "recipe_generator",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "enricher"},
    )

    # After enricher: on fatal, halt. On continue, validate.
    workflow.add_conditional_edges(
        "enricher",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "validator"},
    )

    # After validator: on fatal, halt. On continue, build per-recipe DAGs.
    # Validation failures here mean Claude produced structurally invalid output
    # that Pydantic can't coerce — rare but possible.
    workflow.add_conditional_edges(
        "validator",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "dag_builder"},
    )

    # After dag_builder: on fatal, halt. On continue, merge DAGs into schedule.
    # The main fatal case here is a cyclic depends_on graph detected by
    # NetworkX — this is a generator bug that requires a hard stop.
    workflow.add_conditional_edges(
        "dag_builder",
        error_router,
        {"fatal": "handle_fatal_error", "continue": "dag_merger"},
    )

    # After dag_merger: this is the ONLY node that uses dag_merger_router
    # instead of error_router. dag_merger_router is a 3-way branch:
    #   - "fatal":            unrecoverable scheduling failure → halt
    #   - "continue":         schedule produced successfully → render
    #   - "retry_generation": one-oven irreconcilable conflict → loop back
    #
    # Giving dag_merger its own router (rather than extending error_router
    # with a third branch) keeps error_router clean and makes the retry
    # policy entirely self-contained in one function.
    workflow.add_conditional_edges(
        "dag_merger",
        dag_merger_router,
        {
            "fatal": "handle_fatal_error",
            "continue": "schedule_renderer",
            "retry_generation": "retry_generation",  # triggers the auto-repair loop
        },
    )

    # This is the back-edge that creates the retry loop. LangGraph supports
    # cycles in the graph as long as the checkpointer is present — the
    # checkpointer ensures the loop can be interrupted and resumed.
    # retry_generation resets downstream state fields so recipe_generator
    # starts with a clean slate on the next attempt.
    workflow.add_edge("retry_generation", "recipe_generator")

    # ── Final router after schedule_renderer ─────────────────────────────────
    # final_router is intentionally simpler than error_router: it doesn't
    # inspect individual error.recoverable flags, it just checks whether any
    # errors exist at all. Even a recoverable error accumulated earlier in the
    # pipeline (e.g. a RAG miss) counts as "partial" — the user should know
    # the schedule may have lower-quality enrichment.
    workflow.add_conditional_edges(
        "schedule_renderer",
        final_router,
        {"complete": "mark_complete", "partial": "mark_partial"},
    )

    # ── Terminal edges ────────────────────────────────────────────────────────
    # All three terminal nodes point to END. LangGraph's END sentinel flushes
    # the final checkpoint and signals the awaiting caller (Celery task or
    # test harness) that execution is complete.
    workflow.add_edge("handle_fatal_error", END)
    workflow.add_edge("mark_complete", END)
    workflow.add_edge("mark_partial", END)

    # compile() validates the graph structure (all edges reference registered
    # nodes, no dangling references) and wires up the checkpointer. The
    # checkpointer is what enables mid-graph interruption, resume-from-
    # checkpoint, and the retry loop above. Without it, LangGraph would
    # refuse to compile a graph that contains a cycle.
    return workflow.compile(checkpointer=checkpointer)
