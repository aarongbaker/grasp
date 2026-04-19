from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.authored_recipe import RecipeCookbookRecord
from app.models.catalog import MarketplaceCookbookPublicationRecordView
from app.models.user import (
    MarketplaceCookbookPublicationRecord,
    SellerPayoutAccountRecord,
)


class MarketplacePublicationOwnershipError(Exception):
    """Raised when a publication references a cookbook not owned by the chef."""


async def assert_source_cookbook_owned_by_chef(
    db: AsyncSession,
    *,
    chef_user_id: uuid.UUID,
    source_cookbook_id: uuid.UUID,
) -> RecipeCookbookRecord:
    """Ensure a marketplace publication can only derive from a chef-owned private cookbook.

    Assumption carried forward for S04: a marketplace cookbook listing is a
    publication wrapper around exactly one private RecipeCookbookRecord. The
    source cookbook remains private/planner-oriented; this helper only verifies
    the authorship boundary before later seller flows publish it.
    """

    cookbook = await db.get(RecipeCookbookRecord, source_cookbook_id)
    if cookbook is None or cookbook.user_id != chef_user_id:
        raise MarketplacePublicationOwnershipError(
            "Marketplace publication source cookbook must be owned by the publishing chef"
        )
    return cookbook


async def get_seller_payout_account(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> SellerPayoutAccountRecord | None:
    result = await db.execute(
        select(SellerPayoutAccountRecord).where(SellerPayoutAccountRecord.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def get_marketplace_publication_by_source(
    db: AsyncSession,
    *,
    chef_user_id: uuid.UUID,
    source_cookbook_id: uuid.UUID,
) -> MarketplaceCookbookPublicationRecord | None:
    result = await db.execute(
        select(MarketplaceCookbookPublicationRecord).where(
            MarketplaceCookbookPublicationRecord.chef_user_id == chef_user_id,
            MarketplaceCookbookPublicationRecord.source_cookbook_id == source_cookbook_id,
        )
    )
    return result.scalar_one_or_none()


def build_marketplace_publication_view(
    record: MarketplaceCookbookPublicationRecord,
) -> MarketplaceCookbookPublicationRecordView:
    return MarketplaceCookbookPublicationRecordView(
        marketplace_cookbook_publication_id=record.marketplace_cookbook_publication_id,
        chef_user_id=record.chef_user_id,
        source_cookbook_id=record.source_cookbook_id,
        publication_status=record.publication_status,
        slug=record.slug,
        title=record.title,
        subtitle=record.subtitle,
        description=record.description,
        cover_image_url=record.cover_image_url,
        list_price_cents=record.list_price_cents,
        currency=record.currency,
        recipe_count_snapshot=record.recipe_count_snapshot,
        publication_notes=record.publication_notes,
        published_at=record.published_at.isoformat() if isinstance(record.published_at, datetime) else None,
        unpublished_at=record.unpublished_at.isoformat() if isinstance(record.unpublished_at, datetime) else None,
    )
