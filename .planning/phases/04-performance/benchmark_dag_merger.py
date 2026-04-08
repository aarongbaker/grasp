from __future__ import annotations

from datetime import datetime
import cProfile
import io
from pathlib import Path
import pstats
import statistics
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.graph.nodes.dag_merger import _merge_dags
from app.models.enums import Resource
from app.models.recipe import EnrichedRecipe, RawRecipe, RecipeStep, ValidatedRecipe
from app.models.scheduling import RecipeDAG


def _make_recipe(index: int) -> tuple[RecipeDAG, ValidatedRecipe]:
    name = f"Recipe {index:02d}"
    slug = f"recipe_{index:02d}"
    steps = [
        RecipeStep(
            step_id=f"{slug}_prep",
            description="Prep aromatics",
            duration_minutes=8,
            resource=Resource.HANDS,
        ),
        RecipeStep(
            step_id=f"{slug}_sear",
            description="Sear",
            duration_minutes=12,
            resource=Resource.STOVETOP,
            depends_on=[f"{slug}_prep"],
            stovetop_heat_f=425,
        ),
        RecipeStep(
            step_id=f"{slug}_rest",
            description="Rest",
            duration_minutes=10,
            resource=Resource.PASSIVE,
            depends_on=[f"{slug}_sear"],
        ),
        RecipeStep(
            step_id=f"{slug}_bake",
            description="Bake",
            duration_minutes=20,
            resource=Resource.OVEN,
            depends_on=[f"{slug}_rest"],
            oven_temp_f=375,
        ),
        RecipeStep(
            step_id=f"{slug}_finish",
            description="Finish sauce",
            duration_minutes=9,
            resource=Resource.STOVETOP,
            depends_on=[f"{slug}_bake"],
            stovetop_heat_f=325,
        ),
    ]
    raw = RawRecipe(
        name=name,
        description="benchmark",
        servings=4,
        cuisine="test",
        estimated_total_minutes=59,
        ingredients=[],
        steps=[step.description for step in steps],
    )
    enriched = EnrichedRecipe(source=raw, steps=steps)
    dag = RecipeDAG(
        recipe_name=name,
        recipe_slug=slug,
        steps=[],
        edges=[
            (f"{slug}_prep", f"{slug}_sear"),
            (f"{slug}_sear", f"{slug}_rest"),
            (f"{slug}_rest", f"{slug}_bake"),
            (f"{slug}_bake", f"{slug}_finish"),
        ],
    )
    validated = ValidatedRecipe(source=enriched, validated_at=datetime.now())
    return dag, validated


def _build_dataset(recipe_count: int) -> tuple[list[RecipeDAG], list[ValidatedRecipe]]:
    dags: list[RecipeDAG] = []
    validated: list[ValidatedRecipe] = []
    for index in range(recipe_count):
        dag, recipe = _make_recipe(index)
        dags.append(dag)
        validated.append(recipe)
    return dags, validated


def _run_scaling_table() -> list[dict[str, float | int]]:
    kitchen = {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False}
    rows: list[dict[str, float | int]] = []
    for recipe_count in (4, 8, 12, 16):
        dags, validated = _build_dataset(recipe_count)
        samples_ms: list[float] = []
        total_duration = 0
        for _ in range(40):
            started = time.perf_counter()
            result = _merge_dags(dags, validated, kitchen)
            samples_ms.append((time.perf_counter() - started) * 1000)
            total_duration = result.total_duration_minutes
        rows.append(
            {
                "recipes": recipe_count,
                "steps": recipe_count * 5,
                "median_ms": round(statistics.median(samples_ms), 3),
                "p95_ms": round(statistics.quantiles(samples_ms, n=20)[18], 3),
                "total_duration_minutes": total_duration,
            }
        )
    return rows


def _run_profile() -> str:
    kitchen = {"max_burners": 4, "max_oven_racks": 2, "has_second_oven": False}
    dags, validated = _build_dataset(12)
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(1000):
        _merge_dags(dags, validated, kitchen)
    profiler.disable()

    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(20)
    return stream.getvalue().strip()


def main() -> None:
    print("# Scheduler Benchmark")
    print()
    print("## Scaling")
    for row in _run_scaling_table():
        print(row)

    print()
    print("## Hotspots")
    print(_run_profile())


if __name__ == "__main__":
    main()
