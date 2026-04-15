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

from fastapi import APIRouter, HTTPException

from app.core.deps import CurrentUser, DBSession
from app.models.catalog import (
    CatalogCookbookAudience,
    CatalogCookbookDetail,
    CatalogCookbookDetailResponse,
    CatalogCookbookListResponse,
    CatalogCookbookSummary,
)
from app.models.recipe import Ingredient, RawRecipe, RecipeProvenance
from app.models.user import EntitlementKind
from app.services.access import AccessResolverInput, derive_catalog_cookbook_access
from app.services.catalog_purchases import CatalogPurchaseService
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


async def _build_summary(
    fixture: _CatalogCookbookFixture,
    current_user,
    db,
) -> CatalogCookbookSummary:
    subscription_snapshot = await get_active_subscription_snapshot(db, user_id=current_user.user_id)
    entitlement_grants = await list_user_entitlement_grants(db, user_id=current_user.user_id)
    active_entitlements = {grant.kind for grant in entitlement_grants if grant.is_active}
    diagnostics = build_subscription_diagnostics(subscription_snapshot)
    purchase_service = CatalogPurchaseService(
        known_catalog_cookbook_ids={catalog_fixture.catalog_cookbook_id for catalog_fixture in _CATALOG_COOKBOOKS}
    )
    has_durable_purchase_ownership = await purchase_service.has_owned_catalog_cookbook(
        db,
        user_id=current_user.user_id,
        catalog_cookbook_id=fixture.catalog_cookbook_id,
    )
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
