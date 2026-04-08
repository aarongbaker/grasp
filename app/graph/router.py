"""
graph/router.py
Router functions for conditional edges in the LangGraph state machine.
Built once in Phase 3. Never changed again.

error_router: runs after every node EXCEPT schedule_renderer.
  Checks the last NodeError in state.errors.
  If recoverable=False → "fatal" (pipeline halts at handle_fatal_error)
  Otherwise → "continue" (pipeline proceeds to next node)

  Why check only the last error?
  Each node only appends errors for ITS OWN failures. error_router fires
  immediately after each node. Therefore state.errors[-1] is always from
  the node that just ran. This is a timing guarantee, not a coincidence.
  If a node succeeds with no errors, state.errors is either empty or
  contains only errors from PREVIOUS nodes (all recoverable, since we
  already passed their routers). So checking [-1].recoverable is safe.

normalize_generation_retry_reason: narrows dag_merger resource conflicts into
  the single-oven auto-repair seam. It only returns a retry reason when the
  scheduler proved a one-oven irreconcilable temperature conflict beyond the
  existing tolerance contract and retry budget remains.

final_router: runs after schedule_renderer only.
  Any errors in state.errors (even recoverable) → "partial"
  No errors → "complete"
"""

from app.models.enums import ErrorType
from app.models.pipeline import (
    GRASPState,
    GenerationAttemptRecord,
    GenerationRetryEligibility,
    GenerationRetryReason,
)
from app.models.scheduling import OneOvenConflictSummary


def error_router(state: GRASPState) -> str:
    """
    Returns 'fatal' or 'continue'. Runs after every non-terminal node.
    See module docstring for the timing guarantee that makes [-1] safe.
    """
    errors = state.get("errors", [])
    if not errors:
        return "continue"

    last_error = errors[-1]
    # last_error is a dict (GRASPState stores everything as dicts).
    # Default to False (fatal) if key is missing — fail-safe over fail-open.
    if not last_error.get("recoverable", False):
        return "fatal"

    return "continue"


def normalize_generation_retry_eligibility(state: GRASPState) -> GenerationRetryEligibility:
    """Return a typed retry-routing view for dag_merger repair decisions.

    Policy contract:
    - Only `dag_merger` `RESOURCE_CONFLICT` failures are considered.
    - Metadata must validate as `OneOvenConflictSummary`.
    - Single-oven auto-repair applies only when `has_second_oven` is false and
      the scheduler classified the overlap as `irreconcilable`.
    - `compatible` means oven windows are already within the tolerance contract
      (or otherwise safe), so there is nothing to repair.
    - `resequence_required` is intentionally non-retryable here because the
      scheduler already found a single-oven plan without needing regeneration.
    - Attempt exhaustion is tracked in graph state only; no session row writes
      are involved in this normalization step.
    """
    current_attempt = max(1, int(state.get("generation_attempt", 1)))
    attempt_limit = max(1, int(state.get("generation_attempt_limit", 1)))

    eligibility = GenerationRetryEligibility(
        eligible=False,
        exhausted=current_attempt >= attempt_limit,
        current_attempt=current_attempt,
        attempt_limit=max(current_attempt, attempt_limit),
        retry_reason=None,
    )

    errors = state.get("errors", [])
    if not errors:
        return eligibility

    last_error = errors[-1]
    if last_error.get("node_name") != "dag_merger":
        return eligibility
    if last_error.get("error_type") != ErrorType.RESOURCE_CONFLICT.value:
        return eligibility

    metadata = last_error.get("metadata") or {}
    try:
        summary = OneOvenConflictSummary.model_validate(metadata)
    except Exception:
        return eligibility

    if summary.has_second_oven:
        return eligibility
    if summary.classification != "irreconcilable":
        return eligibility

    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=summary,
        detail=metadata.get("detail") or last_error.get("message", "Resource conflict"),
        attempt=current_attempt,
    )

    return eligibility.model_copy(
        update={
            "eligible": not eligibility.exhausted,
            "retry_reason": retry_reason,
        }
    )


def normalize_generation_retry_reason(state: GRASPState) -> GenerationRetryReason | None:
    """Backward-compatible shim returning only the typed retry reason when eligible."""
    eligibility = normalize_generation_retry_eligibility(state)
    if not eligibility.eligible:
        return None
    return eligibility.retry_reason


def dag_merger_router(state: GRASPState) -> str:
    """Route dag_merger outcomes to continue, retry_generation, or fatal.

    This seam is intentionally narrow: only typed one-oven irreconcilable
    RESOURCE_CONFLICT failures from dag_merger can trigger corrective
    regeneration, and only while the graph-local attempt budget remains.
    Session row status ownership stays unchanged because this router only
    inspects checkpoint state.
    """
    errors = state.get("errors", [])
    if not errors:
        return "continue"

    last_error = errors[-1]
    if last_error.get("node_name") != "dag_merger":
        return "fatal" if not last_error.get("recoverable", False) else "continue"

    eligibility = normalize_generation_retry_eligibility(state)
    if eligibility.eligible and eligibility.retry_reason is not None:
        return "retry_generation"

    return "fatal"


def build_generation_retry_state(state: GRASPState) -> dict:
    """Return the graph-local state patch that records one corrective retry.

    The retry patch deliberately resets downstream graph products so the next
    generation run starts from a clean checkpoint seam, while preserving prior
    errors/token history via LangGraph reducers. No session status writes occur
    here.
    """
    eligibility = normalize_generation_retry_eligibility(state)
    if eligibility.retry_reason is None:
        return {}

    next_attempt = eligibility.current_attempt + 1
    retry_reason = eligibility.retry_reason.model_dump(mode="json")
    history = list(state.get("generation_history", []))
    history.append(
        GenerationAttemptRecord(
            attempt=next_attempt,
            trigger="auto_repair",
            recipe_names=[*eligibility.retry_reason.summary.blocking_recipe_names],
            retry_reason=eligibility.retry_reason,
        ).model_dump(mode="json")
    )

    return {
        "generation_attempt": next_attempt,
        "generation_retry_reason": retry_reason,
        "generation_retry_exhausted": False,
        "generation_history": history,
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        "errors": [],
    }


def final_router(state: GRASPState) -> str:
    """Returns 'partial' or 'complete'. Runs after schedule_renderer only."""
    errors = state.get("errors", [])
    return "partial" if errors else "complete"
