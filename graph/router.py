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

final_router: runs after schedule_renderer only.
  Any errors in state.errors (even recoverable) → "partial"
  No errors → "complete"
"""

from models.pipeline import GRASPState


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


def final_router(state: GRASPState) -> str:
    """Returns 'partial' or 'complete'. Runs after schedule_renderer only."""
    errors = state.get("errors", [])
    return "partial" if errors else "complete"
