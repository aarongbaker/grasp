"""Read-only platform cookbook catalog routes.

This router exposes one explicit backend seam for platform-managed catalog
cookbooks plus derived user access states. It is intentionally separate from:

- /recipe-cookbooks, which manages private chef-owned RecipeCookbookRecord rows
- session planner_cookbook_target, which references those private cookbooks
- ingestion/book chunk models, which remain historical implementation details

The current implementation is fixture-backed rather than persistence-backed so
future slices can build frontend catalog UX against a stable API contract
without coupling to PDF ingestion or billing-provider payloads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any, Iterable

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.deps import CurrentUser, DBSession
from app.core.settings import get_settings
from app.models.authored_recipe import RecipeCookbookRecord
from app.models.catalog import (
    CatalogCookbookAudience,
    CatalogCookbookDetail,
    CatalogCookbookDetailResponse,
    CatalogCookbookListResponse,
    CatalogCookbookOwnershipStatus,
    CatalogCookbookSummary,
    MarketplaceCheckoutResponse,
    MarketplaceCookbookPublicationStatus,
    MarketplaceCookbookPublicationSummary,
    MarketplacePublicationListResponse,
    MarketplacePublicationUpsertRequest,
    MarketplacePurchaseCompletionRequest,
    MarketplacePurchaseCompletionResponse,
)
from app.models.recipe import Ingredient, RawRecipe, RecipeProvenance
from app.models.user import (
    CatalogCookbookOwnershipRecord,
    EntitlementKind,
    MarketplaceCookbookPublicationRecord,
    MarketplaceCookbookPublicationStatus as MarketplaceRecordStatus,
)
from app.services.access import AccessResolverInput, derive_catalog_cookbook_access
from app.services.marketplace_publications import (
    MarketplacePublicationOwnershipError,
    assert_source_cookbook_owned_by_chef,
    build_marketplace_publication_view,
    get_marketplace_publication_by_source,
    get_seller_payout_account,
)
from app.services.stripe_billing import build_billing_service
from app.services.subscriptions import (
    build_subscription_diagnostics,
    get_active_subscription_snapshot,
    list_user_entitlement_grants,
)

router = APIRouter(prefix="/catalog/cookbooks", tags=["catalog"])


@dataclass(frozen=True)
class _CatalogRecipeFixture:
    name: str
    description: str
    cuisine: str
    estimated_total_minutes: int
    ingredients: tuple[tuple[str, str], ...]
    steps: tuple[str, ...]
    course: str | None = None


@dataclass(frozen=True)
class _CatalogCookbookFixture:
    catalog_cookbook_id: uuid.UUID
    slug: str
    title: str
    subtitle: str | None
    description: str
    recipe_count: int
    audience: CatalogCookbookAudience
    sample_recipe_titles: list[str]
    tags: list[str]
    runtime_seed_recipes: tuple[_CatalogRecipeFixture, ...]
    cover_image_url: str | None = None


_CATALOG_COOKBOOKS: tuple[_CatalogCookbookFixture, ...] = (
    _CatalogCookbookFixture(
        catalog_cookbook_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        slug="weeknight-foundations",
        title="Weeknight Foundations",
        subtitle="Fast, dependable platform-managed dinner ideas",
        description=(
            "Platform-managed reference collection for dependable dinners with "
            "minimal lead time. This is catalog inventory, not a private chef cookbook."
        ),
        recipe_count=18,
        audience=CatalogCookbookAudience.INCLUDED,
        sample_recipe_titles=["Skillet Chicken Piccata", "Tomato Braised Chickpeas"],
        tags=["weeknight", "foundations"],
        runtime_seed_recipes=(
            _CatalogRecipeFixture(
                name="Skillet Chicken Piccata",
                description="Bright chicken cutlets with a lemon-caper pan sauce for a dependable weeknight entree.",
                cuisine="Italian-American",
                estimated_total_minutes=40,
                ingredients=(
                    ("chicken cutlets", "8 pieces"),
                    ("flour", "120 g"),
                    ("capers", "3 tbsp"),
                    ("lemons", "2"),
                    ("chicken stock", "240 ml"),
                ),
                steps=(
                    "Season and lightly flour the chicken cutlets.",
                    "Sear the cutlets in olive oil until lightly golden on both sides.",
                    "Simmer briefly with stock, lemon juice, and capers until glossy and cooked through.",
                ),
                course="entree",
            ),
            _CatalogRecipeFixture(
                name="Tomato Braised Chickpeas",
                description="A pantry-friendly chickpea braise with warm spices and herbs.",
                cuisine="Mediterranean",
                estimated_total_minutes=35,
                ingredients=(
                    ("cooked chickpeas", "800 g"),
                    ("crushed tomatoes", "400 g"),
                    ("garlic cloves", "4"),
                    ("olive oil", "3 tbsp"),
                    ("flat-leaf parsley", "1 bunch"),
                ),
                steps=(
                    "Sweat the garlic in olive oil until fragrant.",
                    "Add chickpeas and tomatoes, then simmer until lightly thickened.",
                    "Finish with parsley and olive oil before serving.",
                ),
                course="side",
            ),
        ),
    ),
    _CatalogCookbookFixture(
        catalog_cookbook_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        slug="spring-market-preview",
        title="Spring Market Preview",
        subtitle="Preview dishes from the upcoming seasonal catalog drop",
        description=(
            "Preview-only catalog selection that lets chefs inspect sample dishes "
            "before broader access is granted."
        ),
        recipe_count=6,
        audience=CatalogCookbookAudience.PREVIEW,
        sample_recipe_titles=["Peas with Mint Butter", "Charred Asparagus Toasts"],
        tags=["seasonal", "preview"],
        runtime_seed_recipes=(
            _CatalogRecipeFixture(
                name="Peas with Mint Butter",
                description="Sweet spring peas glazed in mint butter for an early-season side dish.",
                cuisine="British",
                estimated_total_minutes=20,
                ingredients=(("fresh peas", "600 g"), ("unsalted butter", "60 g"), ("mint leaves", "20 g"), ("shallot", "1")),
                steps=(
                    "Sweat the shallot gently in butter.",
                    "Add the peas and cook just until tender and glossy.",
                    "Fold through mint leaves and season before serving.",
                ),
                course="side",
            ),
            _CatalogRecipeFixture(
                name="Charred Asparagus Toasts",
                description="Spring asparagus piled over grilled bread with ricotta and lemon.",
                cuisine="Californian",
                estimated_total_minutes=25,
                ingredients=(("asparagus", "2 bunches"), ("country bread", "8 slices"), ("ricotta", "250 g"), ("lemon", "1")),
                steps=(
                    "Char the asparagus until tender and lightly blistered.",
                    "Toast the bread and spread with ricotta.",
                    "Top with asparagus, lemon zest, and olive oil.",
                ),
                course="appetizer",
            ),
        ),
    ),
    _CatalogCookbookFixture(
        catalog_cookbook_id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        slug="chef-tasting-menus",
        title="Chef Tasting Menus",
        subtitle="Expanded catalog for premium platform entitlements",
        description=(
            "Premium catalog lane for ambitious composed menus. Locked users see "
            "metadata only until their account entitlements change."
        ),
        recipe_count=24,
        audience=CatalogCookbookAudience.PREMIUM,
        sample_recipe_titles=["Scallop Crudo with Green Strawberry", "Coal-Roasted Beets"],
        tags=["premium", "tasting-menu"],
        runtime_seed_recipes=(
            _CatalogRecipeFixture(
                name="Scallop Crudo with Green Strawberry",
                description="A composed raw scallop course with tart fruit and herb oil.",
                cuisine="Contemporary",
                estimated_total_minutes=30,
                ingredients=(("dry scallops", "12"), ("green strawberries", "150 g"), ("chive oil", "2 tbsp"), ("sea salt", "to taste")),
                steps=(
                    "Slice the scallops thinly and keep chilled.",
                    "Dress the green strawberries with salt and a little oil.",
                    "Arrange the scallops with strawberries and spoon over chive oil.",
                ),
                course="appetizer",
            ),
            _CatalogRecipeFixture(
                name="Coal-Roasted Beets",
                description="Deeply roasted beets finished with cultured cream and herbs.",
                cuisine="Contemporary",
                estimated_total_minutes=75,
                ingredients=(("mixed beets", "1.5 kg"), ("cultured cream", "180 g"), ("red wine vinegar", "2 tbsp"), ("dill", "1 bunch")),
                steps=(
                    "Roast the beets until tender and lightly charred.",
                    "Peel and season the warm beets with vinegar and salt.",
                    "Serve with cultured cream and dill.",
                ),
                course="side",
            ),
        ),
    ),
)


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _iter_result_rows(result: Any) -> list[Any]:
    if result is None:
        return []
    all_rows = getattr(result, "all", None)
    if callable(all_rows):
        rows = all_rows()
        if isinstance(rows, Iterable):
            return list(rows)
    scalars = getattr(result, "scalars", None)
    if callable(scalars):
        scalar_result = scalars()
        scalar_all = getattr(scalar_result, "all", None)
        if callable(scalar_all):
            rows = scalar_all()
            if isinstance(rows, Iterable):
                return list(rows)
        scalar_first = getattr(scalar_result, "first", None)
        if callable(scalar_first):
            row = scalar_first()
            return [] if row is None else [row]
    first = getattr(result, "first", None)
    if callable(first):
        row = first()
        return [] if row is None else [row]
    return []


async def _build_summary(
    fixture: _CatalogCookbookFixture,
    current_user,
    db,
) -> CatalogCookbookSummary:
    subscription_snapshot = await get_active_subscription_snapshot(db, user_id=current_user.user_id)
    entitlement_grants = await list_user_entitlement_grants(db, user_id=current_user.user_id)
    active_entitlements = {grant.kind for grant in entitlement_grants if grant.is_active}
    diagnostics = build_subscription_diagnostics(subscription_snapshot)
    ownership_rows = [
        obj
        for (model_class, _), obj in getattr(db, "_store", {}).items()
        if model_class is CatalogCookbookOwnershipRecord
        and getattr(obj, "user_id", None) == current_user.user_id
        and getattr(obj, "catalog_cookbook_id", None) == fixture.catalog_cookbook_id
    ]
    ownership_record = ownership_rows[0] if ownership_rows else None
    has_durable_purchase_ownership = ownership_record is not None
    derived_access = derive_catalog_cookbook_access(
        AccessResolverInput(
            user_id=current_user.user_id,
            audience=fixture.audience,
            catalog_cookbook_id=fixture.catalog_cookbook_id,
            has_preview_entitlement=EntitlementKind.CATALOG_PREVIEW in active_entitlements,
            has_premium_entitlement=EntitlementKind.CATALOG_PREMIUM in active_entitlements,
            has_durable_purchase_ownership=has_durable_purchase_ownership,
            subscription_status=subscription_snapshot.status if subscription_snapshot else None,
            sync_state=subscription_snapshot.sync_state if subscription_snapshot else None,
            diagnostics=diagnostics,
        )
    )
    return CatalogCookbookSummary(
        catalog_cookbook_id=fixture.catalog_cookbook_id,
        slug=fixture.slug,
        title=fixture.title,
        subtitle=fixture.subtitle,
        cover_image_url=fixture.cover_image_url,
        recipe_count=fixture.recipe_count,
        audience=fixture.audience,
        access_state=derived_access.access_state,
        access_state_reason=derived_access.access_state_reason,
        ownership=CatalogCookbookOwnershipStatus(
            is_owned=ownership_record is not None,
            ownership_source=_safe_string(getattr(ownership_record, "ownership_source", None)) if ownership_record is not None else None,
            access_reason=_safe_string(getattr(ownership_record, "access_reason", None)) if ownership_record is not None else None,
        ),
        access_diagnostics={
            "subscription_snapshot_id": str(derived_access.diagnostics.subscription_snapshot_id)
            if derived_access.diagnostics and derived_access.diagnostics.subscription_snapshot_id
            else None,
            "subscription_status": derived_access.diagnostics.subscription_status.value
            if derived_access.diagnostics and derived_access.diagnostics.subscription_status
            else None,
            "sync_state": derived_access.diagnostics.sync_state.value
            if derived_access.diagnostics and derived_access.diagnostics.sync_state
            else None,
            "provider": derived_access.diagnostics.provider if derived_access.diagnostics else None,
        },
    )


async def resolve_catalog_cookbook_access(catalog_cookbook_id: uuid.UUID, current_user, db) -> CatalogCookbookSummary:
    """Resolve one catalog cookbook to canonical backend metadata plus access state."""
    fixture = _find_fixture(catalog_cookbook_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Catalog cookbook not found")
    return await _build_summary(fixture, current_user, db)


def _find_fixture(catalog_cookbook_id: uuid.UUID) -> _CatalogCookbookFixture | None:
    for fixture in _CATALOG_COOKBOOKS:
        if fixture.catalog_cookbook_id == catalog_cookbook_id:
            return fixture
    return None


def _build_catalog_runtime_seed_recipe(
    fixture: _CatalogCookbookFixture,
    recipe_fixture: _CatalogRecipeFixture,
) -> RawRecipe:
    return RawRecipe(
        name=recipe_fixture.name,
        description=recipe_fixture.description,
        servings=4,
        cuisine=recipe_fixture.cuisine,
        estimated_total_minutes=recipe_fixture.estimated_total_minutes,
        ingredients=[Ingredient(name=name, quantity=quantity) for name, quantity in recipe_fixture.ingredients],
        steps=list(recipe_fixture.steps),
        course=recipe_fixture.course,
        provenance=RecipeProvenance(
            kind="library_cookbook",
            source_label=f"catalog:{fixture.slug}:{fixture.title}",
            cookbook_id=str(fixture.catalog_cookbook_id),
        ),
    )


def load_catalog_runtime_seed_recipes(catalog_cookbook_id: uuid.UUID) -> list[RawRecipe]:
    """Return deterministic runtime seed recipes for one catalog cookbook."""

    fixture = _find_fixture(catalog_cookbook_id)
    if fixture is None:
        raise ValueError(f"Catalog cookbook {catalog_cookbook_id} was not found for runtime seeding")

    if not fixture.runtime_seed_recipes:
        raise ValueError(f"Catalog cookbook {fixture.slug!r} has no runtime seed recipes configured")

    return [_build_catalog_runtime_seed_recipe(fixture, recipe) for recipe in fixture.runtime_seed_recipes]


def _publication_to_summary(record: MarketplaceCookbookPublicationRecord) -> MarketplaceCookbookPublicationSummary:
    view = build_marketplace_publication_view(record)
    payload = view.model_dump()
    if isinstance(payload.get("published_at"), str):
        payload["published_at"] = datetime.fromisoformat(payload["published_at"])
    else:
        payload["published_at"] = None
    payload.pop("unpublished_at", None)
    return MarketplaceCookbookPublicationSummary(**payload)


async def _get_publication_or_404(db: DBSession, *, publication_id: uuid.UUID) -> MarketplaceCookbookPublicationRecord:
    publication = await db.get(MarketplaceCookbookPublicationRecord, publication_id)
    if publication is None:
        raise HTTPException(status_code=404, detail="Marketplace publication not found")
    return publication


@router.get("", response_model=CatalogCookbookListResponse)
async def list_catalog_cookbooks(current_user: CurrentUser, db: DBSession):
    """Return the platform-managed catalog seam with derived access states."""

    items = [await _build_summary(fixture, current_user, db) for fixture in _CATALOG_COOKBOOKS]
    return CatalogCookbookListResponse(items=items)


@router.get("/{catalog_cookbook_id}", response_model=CatalogCookbookDetailResponse)
async def get_catalog_cookbook(catalog_cookbook_id: uuid.UUID, current_user: CurrentUser, db: DBSession):
    """Return one platform-managed catalog cookbook detail payload."""

    fixture = _find_fixture(catalog_cookbook_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Catalog cookbook not found")

    summary = await resolve_catalog_cookbook_access(catalog_cookbook_id, current_user, db)
    return CatalogCookbookDetailResponse(
        item=CatalogCookbookDetail(
            **summary.model_dump(),
            description=fixture.description,
            sample_recipe_titles=fixture.sample_recipe_titles,
            tags=fixture.tags,
        )
    )


@router.get("/marketplace/publications", response_model=MarketplacePublicationListResponse)
async def list_marketplace_publications(current_user: CurrentUser, db: DBSession):
    result = await db.exec(
        select(MarketplaceCookbookPublicationRecord)
        .where(MarketplaceCookbookPublicationRecord.chef_user_id == current_user.user_id)
        .order_by(MarketplaceCookbookPublicationRecord.updated_at.desc())
    )
    return MarketplacePublicationListResponse(items=[_publication_to_summary(row) for row in result.all()])


@router.post("/marketplace/publications", response_model=MarketplaceCookbookPublicationSummary, status_code=status.HTTP_201_CREATED)
async def upsert_marketplace_publication(body: MarketplacePublicationUpsertRequest, current_user: CurrentUser, db: DBSession):
    try:
        source_cookbook = await assert_source_cookbook_owned_by_chef(
            db,
            chef_user_id=current_user.user_id,
            source_cookbook_id=body.source_cookbook_id,
        )
    except MarketplacePublicationOwnershipError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    publication = await get_marketplace_publication_by_source(
        db,
        chef_user_id=current_user.user_id,
        source_cookbook_id=body.source_cookbook_id,
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if publication is None:
        publication = MarketplaceCookbookPublicationRecord(
            chef_user_id=current_user.user_id,
            source_cookbook_id=body.source_cookbook_id,
            publication_status=MarketplaceRecordStatus(body.publication_status.value),
            slug=body.slug,
            title=body.title,
            subtitle=body.subtitle,
            description=body.description,
            cover_image_url=body.cover_image_url,
            list_price_cents=body.list_price_cents,
            currency=body.currency.lower(),
            recipe_count_snapshot=len(source_cookbook.recipes) if getattr(source_cookbook, "recipes", None) else 0,
            publication_notes=body.publication_notes,
            published_at=now if body.publication_status == MarketplaceCookbookPublicationStatus.PUBLISHED else None,
            unpublished_at=now if body.publication_status == MarketplaceCookbookPublicationStatus.UNPUBLISHED else None,
        )
        db.add(publication)
    else:
        publication.publication_status = MarketplaceRecordStatus(body.publication_status.value)
        publication.slug = body.slug
        publication.title = body.title
        publication.subtitle = body.subtitle
        publication.description = body.description
        publication.cover_image_url = body.cover_image_url
        publication.list_price_cents = body.list_price_cents
        publication.currency = body.currency.lower()
        publication.recipe_count_snapshot = len(source_cookbook.recipes) if getattr(source_cookbook, "recipes", None) else publication.recipe_count_snapshot
        publication.publication_notes = body.publication_notes
        publication.updated_at = now
        if publication.publication_status == MarketplaceRecordStatus.PUBLISHED:
            publication.published_at = publication.published_at or now
            publication.unpublished_at = None
        elif publication.publication_status == MarketplaceRecordStatus.UNPUBLISHED:
            publication.unpublished_at = now
        db.add(publication)

    await db.commit()
    await db.refresh(publication)
    return _publication_to_summary(publication)


@router.post("/marketplace/publications/{publication_id}/checkout", response_model=MarketplaceCheckoutResponse)
async def create_marketplace_checkout(publication_id: uuid.UUID, current_user: CurrentUser, db: DBSession):
    publication = await _get_publication_or_404(db, publication_id=publication_id)
    if publication.publication_status != MarketplaceRecordStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="Marketplace publication is not available for sale")

    payout_record = await get_seller_payout_account(db, user_id=publication.chef_user_id)
    if payout_record is None or not (payout_record.charges_enabled and payout_record.payouts_enabled):
        raise HTTPException(status_code=409, detail="Seller payout readiness is incomplete for this marketplace cookbook")

    service = build_billing_service(get_settings())
    bundle = await service.create_marketplace_checkout_session(db, buyer=current_user, publication=publication)
    if not isinstance(bundle, (dict, MarketplaceCheckoutResponse)) and not hasattr(bundle, "checkout_url"):
        raise HTTPException(status_code=409, detail="Seller payout readiness is incomplete for this marketplace cookbook")
    if isinstance(bundle, MarketplaceCheckoutResponse):
        return bundle
    if hasattr(bundle, "model_dump"):
        payload = bundle.model_dump()
        if isawaitable(payload):
            payload = await payload
        return MarketplaceCheckoutResponse(**payload)
    return MarketplaceCheckoutResponse.model_validate(bundle)


@router.post("/marketplace/purchases/complete", response_model=MarketplacePurchaseCompletionResponse)
async def complete_marketplace_purchase(body: MarketplacePurchaseCompletionRequest, current_user: CurrentUser, db: DBSession):
    publication = await _get_publication_or_404(db, publication_id=body.marketplace_cookbook_publication_id)
    service = build_billing_service(get_settings())
    bundle = await service.finalize_marketplace_purchase(
        db,
        buyer=current_user,
        publication=publication,
        provider_checkout_ref=body.provider_checkout_ref,
        provider_completion_ref=body.provider_completion_ref,
        checkout_status=body.checkout_status,
        provider=body.provider,
    )
    return MarketplacePurchaseCompletionResponse(
        checkout_status=bundle.checkout_status,
        purchase_state=bundle.purchase_state,
        ownership_granted=bundle.ownership_granted,
        ownership_recorded=bundle.ownership_recorded,
        replayed_completion=bundle.replayed_completion,
        catalog_cookbook_id=bundle.catalog_cookbook_id,
        marketplace_cookbook_publication_id=bundle.marketplace_cookbook_publication_id,
        sale_diagnostics={
            "checkout_status": bundle.checkout_status,
            "purchase_state": bundle.purchase_state,
            "replayed_completion": bundle.replayed_completion,
            "ownership_recorded": bundle.ownership_recorded,
            "ownership_granted": bundle.ownership_granted,
        },
    )
