"""
models/enums.py
All enum definitions for the GRASP system.
Written once in Phase 1a. Never modified by pipeline nodes.
"""

from enum import Enum


class MealType(str, Enum):
    BREAKFAST = "breakfast"
    BRUNCH = "brunch"
    LUNCH = "lunch"
    DINNER = "dinner"
    APPETIZERS = "appetizers"
    SNACKS = "snacks"
    DESSERT = "dessert"
    MEAL_PREP = "meal_prep"


class Occasion(str, Enum):
    CASUAL = "casual"
    DINNER_PARTY = "dinner_party"
    TASTING_MENU = "tasting_menu"
    MEAL_PREP = "meal_prep"


class Resource(str, Enum):
    """
    V1: four coarse buckets. V2 expands to named resources (OVEN_RACK_1, BURNER_3).
    Exclusivity:
      OVEN      — semi-exclusive (conflict if active > max_oven_racks)
      STOVETOP  — semi-exclusive (conflict if active > max_burners)
      PASSIVE   — non-exclusive (always parallelisable)
      HANDS     — exclusive (only one at a time)
    """

    OVEN = "oven"
    STOVETOP = "stovetop"
    PASSIVE = "passive"
    HANDS = "hands"


class EquipmentCategory(str, Enum):
    PRECISION = "precision"
    BAKING = "baking"
    PREP = "prep"
    SPECIALTY = "specialty"


class DocumentType(str, Enum):
    COOKBOOK = "cookbook"
    CULINARY_REFERENCE = "culinary_reference"
    GENERAL_KNOWLEDGE = "general_knowledge"


class ChunkType(str, Enum):
    TECHNIQUE = "technique"
    RECIPE = "recipe"
    RATIO = "ratio"
    TIP = "tip"
    INTRO = "intro"
    # PHILOSOPHY reserved V2 — surfaces narrative/creative direction chunks


class IngestionStatus(str, Enum):
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
    """

    PENDING = "pending"  # neither terminal nor in-progress — awaiting POST /run
    GENERATING = "generating"
    ENRICHING = "enriching"
    VALIDATING = "validating"
    SCHEDULING = "scheduling"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in (SessionStatus.COMPLETE, SessionStatus.PARTIAL, SessionStatus.FAILED)

    @property
    def is_in_progress(self) -> bool:
        return self in (
            SessionStatus.GENERATING,
            SessionStatus.ENRICHING,
            SessionStatus.VALIDATING,
            SessionStatus.SCHEDULING,
        )


class ErrorType(str, Enum):
    LLM_TIMEOUT = "llm_timeout"
    LLM_PARSE_FAILURE = "llm_parse_failure"
    RAG_FAILURE = "rag_failure"
    VALIDATION_FAILURE = "validation_failure"
    DEPENDENCY_RESOLUTION = "dependency_resolution"
    RESOURCE_CONFLICT = "resource_conflict"
    UNKNOWN = "unknown"
