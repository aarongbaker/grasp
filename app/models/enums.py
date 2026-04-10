"""
models/enums.py
All enum definitions for the GRASP system.
Written once in Phase 1a. Never modified by pipeline nodes.
"""

from enum import Enum


class MealType(str, Enum):
    # str base lets FastAPI serialize these as plain strings in JSON responses
    # without needing a custom encoder. Also makes f-string interpolation clean.
    BREAKFAST = "breakfast"
    BRUNCH = "brunch"
    LUNCH = "lunch"
    DINNER = "dinner"
    APPETIZERS = "appetizers"
    SNACKS = "snacks"
    DESSERT = "dessert"
    MEAL_PREP = "meal_prep"


class Occasion(str, Enum):
    # Combined with MealType in RECIPE_COUNT_MAP (generator.py) to derive
    # how many recipes to generate per session.
    CASUAL = "casual"
    DINNER_PARTY = "dinner_party"
    TASTING_MENU = "tasting_menu"
    MEAL_PREP = "meal_prep"


class Resource(str, Enum):
    """
    V1: four coarse buckets. V2 expands to named resources (OVEN_RACK_1, BURNER_3).
    Exclusivity contract — drives dag_merger capacity pools:
      OVEN      — semi-exclusive (conflict if active > max_oven_racks)
      STOVETOP  — semi-exclusive (conflict if active > max_burners)
      PASSIVE   — non-exclusive (always parallelisable — resting, chilling, brining)
      HANDS     — exclusive (only one at a time — the cook's undivided attention)

    PASSIVE is the primary source of time savings in multi-course schedules.
    When a braise is passive, the cook's hands and stovetop are free for other prep.
    """

    OVEN = "oven"
    STOVETOP = "stovetop"
    PASSIVE = "passive"
    HANDS = "hands"


class EquipmentCategory(str, Enum):
    # Displayed in the kitchen setup UI and passed to the generator prompt
    # so Claude knows which advanced techniques are available.
    PRECISION = "precision"   # sous vide, Thermomix
    BAKING = "baking"         # stand mixer, proofing box
    PREP = "prep"             # mandoline, food processor
    SPECIALTY = "specialty"   # pacojet, centrifuge


class DocumentType(str, Enum):
    # Output of ingestion/classifier.py. Stored on BookRecord.document_type.
    # Drives how the state machine weights chunks for RAG retrieval.
    COOKBOOK = "cookbook"
    CULINARY_REFERENCE = "culinary_reference"
    GENERAL_KNOWLEDGE = "general_knowledge"


class ChunkType(str, Enum):
    # Assigned by the state machine (state_machine.py) to each text chunk.
    # enricher.py filters on ALLOWED_RAG_CHUNK_TYPES — only these chunk types
    # are passed as advisory context to Claude during step enrichment.
    TECHNIQUE = "technique"
    RECIPE = "recipe"
    RATIO = "ratio"
    TIP = "tip"
    INTRO = "intro"
    # PHILOSOPHY reserved V2 — surfaces narrative/creative direction chunks


class IngestionStatus(str, Enum):
    # State machine for IngestionJob. Transitions:
    # PENDING → PROCESSING (Celery task picked up) → COMPLETE / FAILED
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class SessionStatus(str, Enum):
    """
    8-state enum. V1 polling; designed for V2 SSE streaming with zero data model changes.
    State ownership (V1.6): GENERATING written by POST /run. Terminal statuses written
    by finalise_session(). In-progress statuses derived by status_projection() from
    checkpoint. Nodes never write Session.status directly.

    Two-tier read contract in GET /sessions/{id}:
      - If status is terminal → read Session row (fast path, no checkpoint needed)
      - If status is in_progress → call status_projection() to read live checkpoint
    """

    PENDING = "pending"      # session created, POST /run not yet called
    GENERATING = "generating"  # generator node running (written by POST /run)
    ENRICHING = "enriching"    # enricher node running — derived, never written
    VALIDATING = "validating"  # validator or dag_builder running — derived, never written
    SCHEDULING = "scheduling"  # dag_merger or renderer running — derived, never written
    COMPLETE = "complete"    # terminal — schedule produced, no dropped recipes
    PARTIAL = "partial"      # terminal — schedule produced, some recipes dropped
    FAILED = "failed"        # terminal — no schedule produced
    CANCELLED = "cancelled"  # terminal — chef cancelled mid-run

    @property
    def is_terminal(self) -> bool:
        # Used in finalise_session() to skip writing over a CANCELLED session.
        # Also used by GET /sessions/{id} to decide whether to read row or checkpoint.
        return self in (SessionStatus.COMPLETE, SessionStatus.PARTIAL, SessionStatus.FAILED, SessionStatus.CANCELLED)

    @property
    def is_in_progress(self) -> bool:
        # Used by the status polling endpoint to decide whether to call
        # status_projection() — only makes sense for running sessions.
        return self in (
            SessionStatus.GENERATING,
            SessionStatus.ENRICHING,
            SessionStatus.VALIDATING,
            SessionStatus.SCHEDULING,
        )


class ErrorType(str, Enum):
    # Maps to specific pipeline failures. Drives:
    #   - error_router routing decisions (RESOURCE_CONFLICT → retry_generation)
    #   - Frontend error message selection (each type has a distinct UI message)
    #   - Log aggregation grouping (error dashboards filter by error_type)
    LLM_TIMEOUT = "llm_timeout"          # Claude API took too long — transient
    LLM_PARSE_FAILURE = "llm_parse_failure"  # structured output didn't match schema
    RAG_FAILURE = "rag_failure"           # Pinecone or enrichment failed
    VALIDATION_FAILURE = "validation_failure"  # Pydantic validation rejected output
    DEPENDENCY_RESOLUTION = "dependency_resolution"  # DAG cycle or dangling dep_id
    RESOURCE_CONFLICT = "resource_conflict"  # oven temperature conflict — may trigger retry
    UNKNOWN = "unknown"                   # catch-all for unexpected exceptions
