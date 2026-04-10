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

# ErrorType is an enum whose .value is stored in the serialized state dict.
# We compare against .value (a string) rather than the enum member because
# LangGraph's checkpointer round-trips state through JSON, which means enum
# instances become plain strings by the time a router reads them.
from app.models.enums import ErrorType

# All four imports are Pydantic models used as typed views over the raw dict
# that LangGraph passes as state. We instantiate them only inside router
# functions — never in the hot path of node execution — so the validation
# overhead is acceptable and buys us structured access with IDE support.
from app.models.pipeline import (
    GRASPState,
    GenerationAttemptRecord,    # one entry written to generation_history per retry loop
    GenerationRetryEligibility, # typed bundle: eligible flag + exhausted flag + reason
    GenerationRetryReason,      # structured description of WHY we're retrying
)

# OneOvenConflictSummary is the typed shape of the metadata blob attached to a
# dag_merger RESOURCE_CONFLICT error. We validate the raw metadata dict against
# this model to confirm the conflict is the specific kind that auto-repair can fix.
from app.models.scheduling import OneOvenConflictSummary


def error_router(state: GRASPState) -> str:
    """
    Returns 'fatal' or 'continue'. Runs after every non-terminal node.
    See module docstring for the timing guarantee that makes [-1] safe.
    """
    errors = state.get("errors", [])

    # No errors means the node completed cleanly. Proceed to the next stage.
    # This is the common path — we only enter the error-inspection block when
    # something actually went wrong.
    if not errors:
        return "continue"

    last_error = errors[-1]
    # last_error is a dict (GRASPState stores everything as dicts).
    # Default to False (fatal) if key is missing — fail-safe over fail-open.
    # If a node somehow forgot to include 'recoverable', we halt rather than
    # silently continuing with corrupted pipeline state.
    if not last_error.get("recoverable", False):
        return "fatal"

    # The last error exists but is recoverable (e.g. a single RAG miss that
    # fell back to Claude-only enrichment). Continue the pipeline — the error
    # stays in state.errors and will cause final_router to emit "partial".
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
    # generation_attempt and generation_attempt_limit are integers in state,
    # but state comes from a JSON checkpoint so we defensively cast. max(1,...)
    # guards against a zero or negative value that would make the exhaustion
    # check nonsensical (0 >= 0 would immediately exhaust before any attempt).
    current_attempt = max(1, int(state.get("generation_attempt", 1)))
    attempt_limit = max(1, int(state.get("generation_attempt_limit", 1)))

    # Build a default "not eligible" result. We return early with this if any
    # guard check fails, rather than using nested ifs. This makes the happy
    # path (where all guards pass) read as a straight line at the bottom.
    eligibility = GenerationRetryEligibility(
        eligible=False,
        exhausted=current_attempt >= attempt_limit,  # True if we've used up all attempts
        current_attempt=current_attempt,
        attempt_limit=max(current_attempt, attempt_limit),  # clamp: limit can't be below current
        retry_reason=None,  # populated only when all guards pass
    )

    errors = state.get("errors", [])
    # No errors at all — dag_merger completed cleanly, nothing to repair.
    if not errors:
        return eligibility

    last_error = errors[-1]

    # Guard 1: The last error must come from dag_merger specifically.
    # If another node's error somehow reached this router (which shouldn't
    # happen given the graph topology), we refuse to trigger a retry.
    if last_error.get("node_name") != "dag_merger":
        return eligibility

    # Guard 2: Must be a RESOURCE_CONFLICT error type. dag_merger can also
    # emit VALIDATION_ERROR or UNEXPECTED_ERROR — those are not repairable
    # by regenerating recipes, so we return not-eligible for them.
    if last_error.get("error_type") != ErrorType.RESOURCE_CONFLICT.value:
        return eligibility

    # Guard 3: The error's metadata must parse as a OneOvenConflictSummary.
    # If the metadata is missing, malformed, or a different conflict shape,
    # model_validate raises and we fall through to the except → not eligible.
    # This is the correct behavior: we only attempt auto-repair when we have
    # a fully typed, understood conflict description.
    metadata = last_error.get("metadata") or {}
    try:
        summary = OneOvenConflictSummary.model_validate(metadata)
    except Exception:
        # Malformed metadata — not safe to attempt auto-repair blindly.
        return eligibility

    # Guard 4: Auto-repair only makes sense when there is no second oven.
    # If a second oven is available, the scheduler should have used it
    # already. A conflict in that context is a scheduler bug, not something
    # recipe regeneration can fix.
    if summary.has_second_oven:
        return eligibility

    # Guard 5: Only "irreconcilable" conflicts trigger a retry. The scheduler
    # uses other classifications ("compatible", "resequence_required") for
    # conflicts it resolved on its own. We only intervene when the scheduler
    # explicitly flagged it as impossible to resolve without different recipes.
    if summary.classification != "irreconcilable":
        return eligibility

    # All guards passed — construct a typed retry reason that captures exactly
    # what conflict we're repairing. This gets written into generation_history
    # so the retry attempt is fully auditable.
    retry_reason = GenerationRetryReason(
        node_name="dag_merger",
        error_type=ErrorType.RESOURCE_CONFLICT,
        summary=summary,
        # Prefer detail from metadata, fall back to the error message string.
        # The detail field is what gets surfaced to the user in a warning.
        detail=metadata.get("detail") or last_error.get("message", "Resource conflict"),
        attempt=current_attempt,
    )

    # model_copy(update=...) is the Pydantic v2 way to produce a new model
    # instance with specific fields overridden — immutable, no mutation of
    # the original eligibility object.
    return eligibility.model_copy(
        update={
            "eligible": not eligibility.exhausted,  # eligible only if attempts remain
            "retry_reason": retry_reason,
        }
    )


def normalize_generation_retry_reason(state: GRASPState) -> GenerationRetryReason | None:
    """Backward-compatible shim returning only the typed retry reason when eligible."""
    # This function existed before GenerationRetryEligibility was introduced.
    # Callers that only care about the reason (not the full eligibility bundle)
    # can use this shim rather than destructuring the eligibility object.
    # dag_merger_router and build_generation_retry_state now both use the
    # full normalize_generation_retry_eligibility internally.
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

    # No errors means the scheduler produced a valid merged schedule.
    # Proceed to the renderer node.
    if not errors:
        return "continue"

    last_error = errors[-1]

    # If the last error didn't come from dag_merger, we're in an unexpected
    # state (e.g. the graph topology changed without updating this router).
    # Fall through to standard recoverable/fatal logic rather than silently
    # misrouting. This mirrors what error_router would do.
    if last_error.get("node_name") != "dag_merger":
        return "fatal" if not last_error.get("recoverable", False) else "continue"

    # Ask the eligibility function whether this specific dag_merger failure
    # qualifies for auto-repair. It enforces all five guards: node name,
    # error type, metadata shape, has_second_oven, and classification.
    eligibility = normalize_generation_retry_eligibility(state)
    if eligibility.eligible and eligibility.retry_reason is not None:
        # The failure is a one-oven irreconcilable conflict AND retry budget
        # remains. Loop back to recipe_generator via retry_generation node.
        return "retry_generation"

    # Either the conflict wasn't the auto-repairable kind, or we've exhausted
    # the retry budget. In both cases this is a hard stop — we can't produce
    # a valid schedule.
    return "fatal"


def build_generation_retry_state(state: GRASPState) -> dict:
    """Return the graph-local state patch that records one corrective retry.

    The retry patch deliberately resets downstream graph products so the next
    generation run starts from a clean checkpoint seam, while preserving prior
    errors/token history via LangGraph reducers. No session status writes occur
    here.
    """
    eligibility = normalize_generation_retry_eligibility(state)

    # If somehow this function is called without a valid retry reason (e.g. the
    # graph topology sends an unexpected state here), return an empty patch
    # rather than corrupting state. The empty dict is a no-op in LangGraph.
    if eligibility.retry_reason is None:
        return {}

    # Compute the attempt number for the upcoming generation pass. The current
    # attempt has already failed; next_attempt is what recipe_generator will
    # run as.
    next_attempt = eligibility.current_attempt + 1

    # Serialize the retry reason to a JSON-safe dict for storage in state.
    # LangGraph checkpoints everything as JSON, so we pre-serialize here
    # rather than relying on LangGraph to handle Pydantic models.
    retry_reason = eligibility.retry_reason.model_dump(mode="json")

    # Append to generation_history (a running audit trail of every retry).
    # We list() the existing history to avoid mutating the state reference
    # directly — LangGraph state should be treated as immutable inside routers.
    history = list(state.get("generation_history", []))
    history.append(
        GenerationAttemptRecord(
            attempt=next_attempt,
            trigger="auto_repair",           # distinguishes user-initiated retries from auto ones
            recipe_names=[*eligibility.retry_reason.summary.blocking_recipe_names],
            retry_reason=eligibility.retry_reason,
        ).model_dump(mode="json")
    )

    # This is the full state patch returned to LangGraph. Fields are split into
    # two categories:
    #
    # PRESERVED via reducer (operator.add):
    #   - errors: returning [] is a no-op append — prior errors stay in state
    #     so the final_router can still see them after the retry completes.
    #
    # RESET to empty/None (replace semantics):
    #   - raw_recipes, enriched_recipes, validated_recipes, recipe_dags,
    #     merged_dag, schedule — all downstream products of recipe_generator
    #     are cleared so the next generation pass starts completely fresh.
    #     Without this reset, the enricher would see a mix of old and new
    #     recipes, and the DAG builder would build on stale data.
    #
    # INCREMENTED / UPDATED:
    #   - generation_attempt: bumped so the next generator call knows which
    #     attempt number it is (used for prompt tuning and logging).
    #   - generation_retry_reason: the typed conflict description, available
    #     to recipe_generator so it can craft a prompt that avoids the same
    #     oven conflict temperature range on the retry.
    #   - generation_retry_exhausted: False because we verified budget remains
    #     before reaching this line.
    #   - generation_history: the full audit trail with the new record appended.
    return {
        "generation_attempt": next_attempt,
        "generation_retry_reason": retry_reason,
        "generation_retry_exhausted": False,
        "generation_history": history,
        # Reset all downstream pipeline products for a clean retry:
        "raw_recipes": [],
        "enriched_recipes": [],
        "validated_recipes": [],
        "recipe_dags": [],
        "merged_dag": None,
        "schedule": None,
        # Empty list = no-op append via operator.add reducer.
        # Prior errors are preserved in state automatically.
        "errors": [],
    }


def final_router(state: GRASPState) -> str:
    """Returns 'partial' or 'complete'. Runs after schedule_renderer only."""
    errors = state.get("errors", [])

    # final_router is deliberately coarser than error_router. It does not
    # inspect individual error.recoverable flags — any error at all (even
    # one that was "recoverable" and allowed the pipeline to continue) results
    # in a "partial" outcome. The reasoning: a recoverable error means some
    # part of the pipeline degraded (e.g. RAG fell back to Claude-only), and
    # the user deserves to know their schedule was produced under degraded
    # conditions. "complete" means the happy path ran end-to-end with zero
    # issues.
    return "partial" if errors else "complete"
