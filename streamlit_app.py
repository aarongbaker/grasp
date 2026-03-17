"""
streamlit_app.py
Test UI for the GRASP pipeline + cookbook ingestion.

Pipeline tab: bypasses Celery, uses MemorySaver, calls graph.ainvoke() directly.
Ingestion tab: bypasses Celery, calls ingestion functions directly.
                Requires `docker compose up -d postgres` for DB writes.

Run: .venv/bin/streamlit run streamlit_app.py
"""

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import streamlit as st
from langgraph.checkpoint.memory import MemorySaver
from graph.graph import build_grasp_graph
from models.enums import MealType, Occasion, Resource
from models.scheduling import NaturalLanguageSchedule


# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="GRASP Test UI", page_icon="🍽️", layout="wide")
st.title("GRASP — Meal Schedule Planner")


@st.cache_resource
def _resolve_dev_user_id() -> str:
    """Look up the dev user UUID from Postgres. Cached for the session."""
    try:
        from sqlalchemy import create_engine, text
        from core.settings import get_settings
        settings = get_settings()
        # Convert async URL to sync for this one-shot query
        sync_url = settings.database_url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT user_id FROM user_profiles WHERE email = :email"),
                {"email": "dev@grasp.local"},
            ).fetchone()
        engine.dispose()
        if row:
            return str(row[0])
    except Exception:
        pass
    return ""

tab_pipeline, tab_ingest = st.tabs(["Plan a Meal", "Ingest Cookbooks"])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1: PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

with tab_pipeline:
    st.caption("Claude + Pinecone RAG · LangGraph pipeline")

    # ── Sidebar: pipeline input ──────────────────────────────────────────────
    with st.sidebar:
        st.header("Dinner Concept")

        free_text = st.text_area(
            "Describe your meal",
            value="A rustic Italian dinner: handmade pasta with bolognese, "
                  "a simple arugula salad, and tiramisu for dessert.",
            height=120,
        )
        guest_count = st.number_input("Guest count", min_value=1, max_value=50, value=4)
        meal_type = st.selectbox("Meal type", [m.value for m in MealType], index=3)
        occasion = st.selectbox("Occasion", [o.value for o in Occasion], index=1)

        dietary_input = st.text_input("Dietary restrictions (comma-separated)", "")
        dietary_restrictions = [d.strip() for d in dietary_input.split(",") if d.strip()]

        st.divider()
        st.header("Kitchen Config")
        max_burners = st.number_input("Stovetop burners", min_value=1, max_value=8, value=4)
        max_oven_racks = st.number_input("Oven racks", min_value=1, max_value=4, value=2)

        st.divider()
        run_button = st.button("Run Pipeline", type="primary", use_container_width=True)

    # ── Pipeline execution ───────────────────────────────────────────────────
    PIPELINE_NODES = [
        "recipe_generator", "rag_enricher", "validator",
        "dag_builder", "dag_merger", "schedule_renderer",
    ]
    NODE_LABELS = {
        "recipe_generator": "Generating recipes...",
        "rag_enricher": "Enriching with cookbook RAG...",
        "validator": "Validating recipes...",
        "dag_builder": "Building dependency graphs...",
        "dag_merger": "Merging & scheduling...",
        "schedule_renderer": "Rendering timeline...",
    }

    async def run_pipeline_streaming(concept: dict, kitchen: dict, user_id: str, progress_bar, status_text):
        """Stream graph execution with per-node progress updates."""
        checkpointer = MemorySaver()
        graph = build_grasp_graph(checkpointer)

        initial_state = {
            "concept": concept,
            "kitchen_config": kitchen,
            "equipment": [],
            "user_id": user_id,
            "raw_recipes": [],
            "enriched_recipes": [],
            "validated_recipes": [],
            "recipe_dags": [],
            "merged_dag": None,
            "schedule": None,
            "errors": [],
            "test_mode": None,
        }

        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}

        final_state = None
        completed = 0
        total = len(PIPELINE_NODES)

        async for event in graph.astream(initial_state, config=config):
            # event is {node_name: state_update}
            for node_name in event:
                if node_name in NODE_LABELS:
                    completed += 1
                    progress_bar.progress(completed / total, text=NODE_LABELS.get(node_name, node_name))
                final_state = event[node_name] if isinstance(event[node_name], dict) else final_state

        progress_bar.progress(1.0, text="Done!")
        # Get final snapshot from checkpointer
        snapshot = await graph.aget_state(config)
        return dict(snapshot.values)

    if run_button:
        concept = {
            "free_text": free_text,
            "guest_count": guest_count,
            "meal_type": meal_type,
            "occasion": occasion,
            "dietary_restrictions": dietary_restrictions,
        }
        kitchen = {
            "max_burners": max_burners,
            "max_oven_racks": max_oven_racks,
        }

        progress_bar = st.progress(0, text="Starting pipeline...")
        status_container = st.empty()

        try:
            dev_user_id = _resolve_dev_user_id()
            final_state = asyncio.run(
                run_pipeline_streaming(concept, kitchen, dev_user_id, progress_bar, status_container)
            )
            st.session_state["final_state"] = final_state

            errors = final_state.get("errors", [])
            if final_state.get("schedule"):
                if errors:
                    status_container.warning("Pipeline completed with warnings")
                else:
                    status_container.success("Pipeline completed successfully!")
            else:
                status_container.error("Pipeline failed — no schedule produced")
        except Exception as e:
            progress_bar.empty()
            st.error(f"Pipeline crashed: {e}")
            st.stop()

    # ── Results display ──────────────────────────────────────────────────────
    if "final_state" not in st.session_state:
        st.info("Configure your meal in the sidebar and click **Run Pipeline**.")
        st.stop()

    state = st.session_state["final_state"]
    errors = state.get("errors", [])

    # ── Errors banner ────────────────────────────────────────────────────────
    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=False):
            for err in errors:
                st.warning(
                    f"**{err.get('node_name', '?')}** ({err.get('error_type', '?')}): "
                    f"{err.get('message', '?')}"
                )

    # ── Schedule ──────────────────────────────────────────────────────────────
    schedule_dict = state.get("schedule")
    if not schedule_dict:
        st.error("No schedule produced. Check errors above.")
        st.stop()

    schedule = NaturalLanguageSchedule.model_validate(schedule_dict)
    from models.recipe import RawRecipe, EnrichedRecipe, ValidatedRecipe

    # ── Tabs: Schedule | Recipes | Debug ──────────────────────────────────────
    tab_schedule, tab_recipes, tab_debug = st.tabs(["Schedule", "Recipes", "Debug"])

    # ══════════════════════════════════════════════════════════════════════════
    # SCHEDULE TAB
    # ══════════════════════════════════════════════════════════════════════════
    with tab_schedule:
        # ── Overview metrics ──────────────────────────────────────────────────
        raw_recipes = state.get("raw_recipes", [])
        enriched_recipes = state.get("enriched_recipes", [])
        total_steps = sum(len(er.get("steps", [])) for er in enriched_recipes)
        total_ingredients = sum(len(r.get("ingredients", [])) for r in raw_recipes)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Duration", f"{schedule.total_duration_minutes} min")
        m2.metric("Recipes", len(raw_recipes))
        m3.metric("Steps", total_steps)
        m4.metric("Ingredients", total_ingredients)

        st.markdown(f">{schedule.summary}")
        if schedule.error_summary:
            st.warning(schedule.error_summary)

        st.divider()

        # ── Timeline ──────────────────────────────────────────────────────────
        RESOURCE_BADGES = {
            "HANDS": ":orange[HANDS]",
            "STOVETOP": ":red[STOVETOP]",
            "OVEN": ":red[OVEN]",
            "PASSIVE": ":blue[PASSIVE]",
        }

        prep_ahead = [e for e in schedule.timeline if e.is_prep_ahead]
        main_timeline = [e for e in schedule.timeline if not e.is_prep_ahead]

        if prep_ahead:
            st.subheader("Prep Ahead")
            for entry in prep_ahead:
                badge = RESOURCE_BADGES.get(entry.resource.value, entry.resource.value)
                with st.container(border=True):
                    st.markdown(
                        f"**{entry.recipe_name}** | {badge} | "
                        f"`{entry.duration_minutes} min`"
                    )
                    st.write(entry.action)
                    if entry.prep_ahead_window:
                        st.caption(f"Window: {entry.prep_ahead_window}")

        st.subheader("Cook Day Timeline")

        # Group by time offset for cleaner display
        for entry in main_timeline:
            badge = RESOURCE_BADGES.get(entry.resource.value, entry.resource.value)
            duration_str = f"`{entry.duration_minutes} min`"
            if entry.duration_max and entry.duration_max > entry.duration_minutes:
                duration_str = f"`{entry.duration_minutes}-{entry.duration_max} min`"

            with st.container(border=True):
                cols = st.columns([1, 5, 2])
                cols[0].markdown(f"### {entry.label}")
                cols[1].markdown(
                    f"**{entry.recipe_name}** | {badge}\n\n"
                    f"{entry.action}"
                )
                cols[2].markdown(f"{duration_str}")
                if entry.heads_up:
                    st.caption(f"Note: {entry.heads_up}")

    # ══════════════════════════════════════════════════════════════════════════
    # RECIPES TAB — analytical notebook layout
    # ══════════════════════════════════════════════════════════════════════════
    with tab_recipes:
        raw_recipes = state.get("raw_recipes", [])
        enriched_recipes = state.get("enriched_recipes", [])
        validated_recipes = state.get("validated_recipes", [])

        # Build lookup: recipe name → enriched steps + validation
        enriched_by_name = {}
        for er in enriched_recipes:
            source = er.get("source", {})
            enriched_by_name[source.get("name", "")] = er

        validated_by_name = {}
        for vr in validated_recipes:
            source = vr.get("source", {}).get("source", {})
            validated_by_name[source.get("name", "")] = vr

        for i, raw in enumerate(raw_recipes, 1):
            recipe_name = raw.get("name", "Unknown")
            enriched = enriched_by_name.get(recipe_name, {})
            validated = validated_by_name.get(recipe_name, {})
            ingredients = raw.get("ingredients", [])
            enriched_steps = enriched.get("steps", [])
            chef_notes = enriched.get("chef_notes", "")
            techniques = enriched.get("techniques_used", [])
            rag_sources = enriched.get("rag_sources", [])
            warnings = validated.get("warnings", [])
            passed = validated.get("passed", True)

            st.markdown(f"## {i}. {recipe_name}")

            # ── Recipe header metrics ─────────────────────────────────────
            hdr1, hdr2, hdr3, hdr4 = st.columns(4)
            hdr1.metric("Cuisine", raw.get("cuisine", "—"))
            hdr2.metric("Servings", raw.get("servings", "—"))
            hdr3.metric("Est. Total", f"{raw.get('estimated_total_minutes', '?')} min")
            hdr4.metric("Steps", len(enriched_steps))

            st.caption(raw.get("description", ""))

            if not passed:
                st.error("Validation failed")
            if warnings:
                for w in warnings:
                    st.warning(w)

            # ── Master ingredient list ────────────────────────────────────
            with st.expander("Ingredients", expanded=True):
                ing_data = []
                for ing in ingredients:
                    prep = ing.get("preparation", "")
                    ing_data.append({
                        "Ingredient": ing.get("name", ""),
                        "Qty": ing.get("quantity", ""),
                        "Prep": prep if prep else "—",
                    })
                if ing_data:
                    st.dataframe(
                        ing_data,
                        use_container_width=True,
                        hide_index=True,
                    )

            # ── Steps — each as a bordered card ──────────────────────────
            st.markdown("#### Procedure")
            for j, step in enumerate(enriched_steps, 1):
                resource_val = step.get("resource", "HANDS")
                badge = RESOURCE_BADGES.get(resource_val, resource_val)
                dur = step.get("duration_minutes", "?")
                dur_max = step.get("duration_max")
                if dur_max and dur_max > dur:
                    time_str = f"{dur}–{dur_max} min"
                else:
                    time_str = f"{dur} min"

                with st.container(border=True):
                    # Step header row
                    st.markdown(
                        f"**Step {j}** | {badge} | `{time_str}`"
                    )

                    # Description
                    st.write(step.get("description", ""))

                    # Dependencies
                    deps = step.get("depends_on", [])
                    if deps:
                        st.caption(f"Depends on: {', '.join(deps)}")

                    # Prep-ahead flag
                    if step.get("can_be_done_ahead"):
                        ahead_info = step.get("prep_ahead_window", "")
                        ahead_notes = step.get("prep_ahead_notes", "")
                        label = "Can be done ahead"
                        if ahead_info:
                            label += f" ({ahead_info})"
                        st.caption(label)
                        if ahead_notes:
                            st.caption(f"  {ahead_notes}")

            # ── Chef notes + techniques ───────────────────────────────────
            if chef_notes or techniques:
                st.markdown("#### Notes")
                if chef_notes:
                    st.info(chef_notes)
                if techniques:
                    st.caption(f"Techniques: {' / '.join(techniques)}")

            # ── RAG sources ───────────────────────────────────────────────
            if rag_sources:
                st.caption(f"RAG sources: {len(rag_sources)} cookbook chunk(s) used")

            st.divider()

    # ══════════════════════════════════════════════════════════════════════════
    # DEBUG TAB — raw JSON for inspection
    # ══════════════════════════════════════════════════════════════════════════
    with tab_debug:
        debug_recipes_tab, debug_dags, debug_merged, debug_raw = st.tabs([
            "Recipes", "DAGs", "Merged Schedule", "Raw State",
        ])

        with debug_recipes_tab:
            for r in state.get("raw_recipes", []):
                with st.expander(r.get("name", "Unknown")):
                    st.json(r)

        with debug_dags:
            for dag in state.get("recipe_dags", []):
                with st.expander(dag.get("recipe_name", "Unknown")):
                    st.json(dag)

        with debug_merged:
            merged = state.get("merged_dag")
            if merged:
                st.json(merged)
            else:
                st.write("No merged DAG.")

        with debug_raw:
            st.json(state)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2: INGEST COOKBOOKS
# ══════════════════════════════════════════════════════════════════════════════

with tab_ingest:
    st.caption("Requires `docker compose up -d postgres` · writes to Postgres + Pinecone")

    st.markdown("""
    Upload cookbook PDFs to build your personal RAG knowledge base.
    The enricher will use these when planning meals.
    """)

    # ── DB connection helper ─────────────────────────────────────────────────
    async def _get_db_session():
        """Create a standalone async DB session for ingestion."""
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlmodel import SQLModel
        from core.settings import get_settings

        settings = get_settings()
        engine = create_async_engine(settings.database_url, echo=False)

        import models.user       # noqa: F401
        import models.session    # noqa: F401
        import models.ingestion  # noqa: F401
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        SessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        return engine, SessionLocal

    async def _ensure_dev_user(db) -> uuid.UUID:
        """Create or reuse a default dev user."""
        from sqlmodel import select
        from models.user import UserProfile, KitchenConfig

        DEV_EMAIL = "dev@grasp.local"
        result = await db.execute(select(UserProfile).where(UserProfile.email == DEV_EMAIL))
        user = result.scalars().first()
        if user:
            return user.user_id

        kitchen = KitchenConfig()
        db.add(kitchen)
        await db.flush()

        user = UserProfile(name="Dev Chef", email=DEV_EMAIL, kitchen_config_id=kitchen.kitchen_config_id)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.user_id

    async def _ingest_pdf(pdf_bytes: bytes, filename: str, user_id: uuid.UUID, db) -> dict:
        """Ingest a single PDF through the full pipeline."""
        from ingestion.rasteriser import rasterise_and_ocr_pdf
        from ingestion.classifier import classify_document
        from ingestion.state_machine import run_state_machine
        from ingestion.embedder import embed_and_upsert_chunks
        from models.ingestion import BookRecord

        book_id = str(uuid.uuid4())

        book = BookRecord(book_id=uuid.UUID(book_id), user_id=user_id, title=filename)
        db.add(book)
        await db.flush()

        # OCR
        pages = await rasterise_and_ocr_pdf(pdf_bytes, book_id, str(user_id), db)

        # Classify
        first_pages_text = " ".join(p["text"] for p in pages[:3])
        doc_type = await classify_document(first_pages_text)
        book.document_type = doc_type

        # Chunk
        chunks = run_state_machine(pages)

        # Embed + upsert
        count = await embed_and_upsert_chunks(chunks, book_id, str(user_id), db) if chunks else 0

        book.total_pages = len(pages)
        book.total_chunks = count
        db.add(book)
        await db.commit()

        return {
            "filename": filename,
            "pages": len(pages),
            "chunks": count,
            "type": doc_type.value,
        }

    # ── Upload UI ────────────────────────────────────────────────────────────
    uploaded_files = st.file_uploader(
        "Upload cookbook PDFs",
        type=["pdf"],
        accept_multiple_files=True,
    )

    if uploaded_files:
        st.write(f"**{len(uploaded_files)}** file(s) selected")

    ingest_button = st.button("Ingest", type="primary", disabled=not uploaded_files)

    if ingest_button and uploaded_files:
        async def _run_ingestion():
            engine, SessionLocal = await _get_db_session()
            results = []
            failures = []

            async with SessionLocal() as db:
                user_id = await _ensure_dev_user(db)
                st.session_state["ingest_user_id"] = str(user_id)

                for i, uploaded in enumerate(uploaded_files):
                    pdf_bytes = uploaded.read()
                    try:
                        result = await _ingest_pdf(pdf_bytes, uploaded.name, user_id, db)
                        results.append(result)
                    except Exception as exc:
                        failures.append({"filename": uploaded.name, "error": str(exc)})

            await engine.dispose()
            return results, failures

        with st.status(f"Ingesting {len(uploaded_files)} cookbook(s)...", expanded=True) as status:
            try:
                results, failures = asyncio.run(_run_ingestion())
                st.session_state["ingest_results"] = results
                st.session_state["ingest_failures"] = failures

                if failures:
                    status.update(label=f"Ingestion done — {len(failures)} failure(s)", state="complete")
                else:
                    status.update(label="Ingestion complete!", state="complete")
            except Exception as e:
                st.error(f"Ingestion crashed: {e}")
                status.update(label="Ingestion failed", state="error")

    # ── Results display ──────────────────────────────────────────────────────
    if "ingest_results" in st.session_state:
        results = st.session_state["ingest_results"]
        failures = st.session_state.get("ingest_failures", [])

        if results:
            st.success(f"Ingested {len(results)} cookbook(s)")
            total_pages = sum(r["pages"] for r in results)
            total_chunks = sum(r["chunks"] for r in results)

            cols = st.columns(3)
            cols[0].metric("Books", len(results))
            cols[1].metric("Pages", total_pages)
            cols[2].metric("Chunks in Pinecone", total_chunks)

            for r in results:
                st.write(f"- **{r['filename']}** — {r['pages']} pages, {r['chunks']} chunks ({r['type']})")

        if failures:
            st.error(f"{len(failures)} failed:")
            for f in failures:
                st.write(f"- **{f['filename']}**: {f['error']}")

        if "ingest_user_id" in st.session_state:
            st.info(f"User ID for RAG queries: `{st.session_state['ingest_user_id']}`")
