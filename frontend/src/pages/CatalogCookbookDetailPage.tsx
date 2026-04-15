import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { getCatalogCookbook } from '../api/catalog';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Skeleton } from '../components/shared/Skeleton';
import type {
  CatalogAccessDiagnostics,
  CatalogCookbookAccessState,
  CatalogCookbookDetail,
  CatalogCookbookOwnershipStatus,
  MarketplaceCookbookPublicationSummary,
  MarketplaceSaleDiagnostics,
  PlannerCatalogCookbookReference,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './CatalogCookbookDetailPage.module.css';

type DetailStatus = 'loading' | 'ready' | 'error';

interface AccessRemediationAction {
  label: string;
  to: string;
}

interface AccessRemediation {
  heading: string;
  body: string;
  detail: string;
  action: AccessRemediationAction;
}

const plannerPathway = pathwayByKey['generated-planner'];
const catalogPathway = pathwayByKey.catalog;
const profilePath = '/profile';

function isCatalogAccessDiagnostics(value: unknown): value is CatalogAccessDiagnostics {
  if (value == null) {
    return true;
  }
  if (!value || typeof value !== 'object') {
    return false;
  }

  const diagnostics = value as Partial<CatalogAccessDiagnostics>;
  return (
    (diagnostics.subscription_snapshot_id == null || typeof diagnostics.subscription_snapshot_id === 'string') &&
    (diagnostics.subscription_status == null || typeof diagnostics.subscription_status === 'string') &&
    (diagnostics.sync_state == null || typeof diagnostics.sync_state === 'string') &&
    (diagnostics.provider == null || typeof diagnostics.provider === 'string')
  );
}

function isCatalogCookbookOwnershipStatus(value: unknown): value is CatalogCookbookOwnershipStatus {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const ownership = value as Partial<CatalogCookbookOwnershipStatus>;
  return (
    typeof ownership.is_owned === 'boolean' &&
    (ownership.ownership_source == null || typeof ownership.ownership_source === 'string') &&
    (ownership.access_reason == null || typeof ownership.access_reason === 'string')
  );
}

function isMarketplacePublicationSummary(value: unknown): value is MarketplaceCookbookPublicationSummary {
  if (value == null) {
    return true;
  }
  if (!value || typeof value !== 'object') {
    return false;
  }

  const publication = value as Partial<MarketplaceCookbookPublicationSummary>;
  return (
    typeof publication.marketplace_cookbook_publication_id === 'string' &&
    typeof publication.source_cookbook_id === 'string' &&
    typeof publication.publication_status === 'string' &&
    typeof publication.slug === 'string' &&
    typeof publication.title === 'string' &&
    typeof publication.description === 'string' &&
    typeof publication.list_price_cents === 'number' &&
    typeof publication.currency === 'string' &&
    typeof publication.recipe_count_snapshot === 'number'
  );
}

function isMarketplaceSaleDiagnostics(value: unknown): value is MarketplaceSaleDiagnostics {
  if (value == null) {
    return true;
  }
  if (!value || typeof value !== 'object') {
    return false;
  }

  const diagnostics = value as Partial<MarketplaceSaleDiagnostics>;
  return (
    typeof diagnostics.checkout_status === 'string' &&
    typeof diagnostics.purchase_state === 'string' &&
    typeof diagnostics.replayed_completion === 'boolean' &&
    typeof diagnostics.ownership_recorded === 'boolean' &&
    typeof diagnostics.ownership_granted === 'boolean'
  );
}

function isCatalogCookbookDetail(value: unknown): value is CatalogCookbookDetail {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const item = value as Partial<CatalogCookbookDetail>;
  return (
    typeof item.catalog_cookbook_id === 'string' &&
    item.catalog_cookbook_id.length > 0 &&
    typeof item.slug === 'string' &&
    typeof item.title === 'string' &&
    typeof item.recipe_count === 'number' &&
    typeof item.access_state === 'string' &&
    typeof item.access_state_reason === 'string' &&
    isCatalogCookbookOwnershipStatus(item.ownership) &&
    isCatalogAccessDiagnostics(item.access_diagnostics) &&
    typeof item.description === 'string' &&
    Array.isArray(item.sample_recipe_titles) &&
    Array.isArray(item.tags) &&
    (item.publication == null || isMarketplacePublicationSummary(item.publication)) &&
    (item.payout_onboarding_status == null || typeof item.payout_onboarding_status === 'string') &&
    (item.can_accept_sales == null || typeof item.can_accept_sales === 'boolean') &&
    isMarketplaceSaleDiagnostics(item.sale_diagnostics)
  );
}

function getAccessBadgeLabel(detail: CatalogCookbookDetail): string {
  if (detail.ownership.is_owned) {
    return 'Owned';
  }

  switch (detail.access_state) {
    case 'included':
      return 'Included';
    case 'preview':
      return 'Preview';
    case 'locked':
      return 'Locked';
    default:
      return detail.access_state;
  }
}

function getAccessBadgeClass(detail: CatalogCookbookDetail): string {
  return detail.ownership.is_owned ? styles.access_owned : styles[`access_${detail.access_state}`];
}

function getAccessRemediation(detail: CatalogCookbookDetail): AccessRemediation {
  const syncFailed = detail.access_diagnostics?.sync_state === 'failed';
  const ownedReason = detail.ownership.access_reason ?? 'You own this platform cookbook, so access remains available through the owned catalog lane.';

  if (detail.ownership.is_owned) {
    return {
      heading: 'Owned in your catalog lane',
      body: ownedReason,
      detail: 'This cookbook stays available for planner handoff through durable platform ownership, separate from any private cookbook folders or subscription snapshots.',
      action: {
        label: 'Plan from your owned cookbook',
        to: plannerPathway.to,
      },
    };
  }

  if (detail.access_state === 'included') {
    return {
      heading: 'Included in your catalog lane',
      body: detail.access_state_reason,
      detail: 'You can hand this cookbook off to dinner planning immediately from here.',
      action: {
        label: 'Plan from this cookbook',
        to: plannerPathway.to,
      },
    };
  }

  if (detail.access_state === 'preview') {
    return {
      heading: 'Preview access is available',
      body: detail.access_state_reason,
      detail: 'Preview cookbooks can still seed the planner, so you can test the lane before deciding whether to unlock more access.',
      action: {
        label: 'Plan from preview access',
        to: plannerPathway.to,
      },
    };
  }

  if (syncFailed) {
    return {
      heading: 'Catalog access is waiting on a refresh',
      body: detail.access_state_reason,
      detail: 'Your account access needs to finish refreshing before this cookbook can move into planning. Review account access, then return here once the catalog state settles.',
      action: {
        label: 'Review account access',
        to: profilePath,
      },
    };
  }

  return {
    heading: 'This cookbook is currently locked',
    body: detail.access_state_reason,
    detail: 'If you still need this cookbook in the planner, review your account access first. Otherwise, return to the catalog and choose an included, preview, or already-owned cookbook.',
    action: {
      label: 'Review account access',
      to: profilePath,
    },
  };
}

function getPlannerHandoff(detail: CatalogCookbookDetail): PlannerCatalogCookbookReference {
  return {
    catalog_cookbook_id: detail.catalog_cookbook_id,
    slug: detail.slug,
    title: detail.title,
    access_state: detail.access_state,
    access_state_reason: detail.access_state_reason,
    ownership: detail.ownership,
    access_diagnostics: detail.access_diagnostics,
  };
}

function getPlannerLinkLabel(detail: CatalogCookbookDetail): string {
  if (detail.ownership.is_owned) {
    return 'Plan from owned catalog cookbook';
  }

  switch (detail.access_state as CatalogCookbookAccessState) {
    case 'included':
      return 'Plan from included catalog cookbook';
    case 'preview':
      return 'Plan from preview catalog cookbook';
    case 'locked':
      return 'Review planner guidance for locked cookbook';
    default:
      return 'Return to dinner planner';
  }
}

function getSellerListingCopy(detail: CatalogCookbookDetail): string | null {
  if (detail.publication?.publication_status === 'published') {
    return `Published for sale at $${(detail.publication.list_price_cents / 100).toFixed(2)} ${detail.publication.currency.toUpperCase()} with seller payout state ${detail.payout_onboarding_status ?? 'unknown'}.`;
  }
  if (detail.can_accept_sales === false && detail.payout_onboarding_status) {
    return 'Seller payout onboarding is not ready yet, so this cookbook is not currently available for marketplace sale.';
  }
  return null;
}

function getSaleOutcomeCopy(detail: CatalogCookbookDetail): string | null {
  if (detail.sale_diagnostics?.ownership_granted) {
    return 'Marketplace purchase completion granted durable ownership through the existing catalog ownership seam.';
  }
  return null;
}

export function CatalogCookbookDetailPage() {
  const navigate = useNavigate();
  const { catalogCookbookId } = useParams<{ catalogCookbookId: string }>();
  const [status, setStatus] = useState<DetailStatus>('loading');
  const [item, setItem] = useState<CatalogCookbookDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    const trimmedId = catalogCookbookId?.trim() ?? '';
    setStatus('loading');
    setError(null);

    if (!trimmedId) {
      setItem(null);
      setError('Choose a catalog cookbook from the browse page before opening details.');
      setStatus('error');
      return;
    }

    try {
      const response = await getCatalogCookbook(trimmedId);
      if (!response || !isCatalogCookbookDetail(response.item)) {
        throw new Error('Catalog cookbook detail came back in an unexpected shape. Please retry in a moment.');
      }

      setItem(response.item);
      setStatus('ready');
    } catch (err) {
      setItem(null);
      setError(getErrorMessage(err, 'Could not load this cookbook detail.'));
      setStatus('error');
    }
  }, [catalogCookbookId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const accessRemediation = useMemo(() => (item ? getAccessRemediation(item) : null), [item]);
  const plannerHandoff = useMemo(() => (item ? getPlannerHandoff(item) : null), [item]);
  const ownershipCopy = item?.ownership.is_owned
    ? item.ownership.access_reason ?? 'This cookbook is durably owned through the platform marketplace.'
    : null;
  const sellerListingCopy = item ? getSellerListingCopy(item) : null;
  const saleOutcomeCopy = item ? getSaleOutcomeCopy(item) : null;

  return (
    <div className={styles.page}>
      {status === 'loading' ? (
        <section className={styles.loadingState} aria-label="Loading cookbook detail">
          <Skeleton variant="heading" width="40%" />
          <Skeleton variant="text" width="70%" />
          <Skeleton variant="card" count={2} />
        </section>
      ) : status === 'error' ? (
        <section className={styles.errorState} aria-live="polite">
          <p className={styles.eyebrow}>Catalog detail unavailable</p>
          <h1 className={styles.title}>This cookbook detail could not be shown.</h1>
          <p className={styles.subtitle}>{error ?? 'Could not load this cookbook detail.'}</p>
          <div className={styles.actionRow}>
            <Button variant="secondary" onClick={() => void fetchDetail()}>
              Try again
            </Button>
            <Link to={catalogPathway.to} className={styles.inlineLink}>
              Back to catalog
            </Link>
          </div>
        </section>
      ) : item ? (
        <>
          <header className={styles.hero}>
            <div className={styles.heroCopy}>
              <p className={styles.eyebrow}>Platform catalog detail</p>
              <div className={styles.heroHeaderRow}>
                <h1 className={styles.title}>{item.title}</h1>
                <span className={`${styles.accessBadge} ${getAccessBadgeClass(item)}`}>
                  {getAccessBadgeLabel(item)}
                </span>
              </div>
              {item.subtitle ? <p className={styles.subtitle}>{item.subtitle}</p> : null}
              <p className={styles.description}>{item.description}</p>
              {ownershipCopy ? <p className={styles.ownershipText}>{ownershipCopy}</p> : null}
              {sellerListingCopy ? <p className={styles.publicationText}>{sellerListingCopy}</p> : null}
              {saleOutcomeCopy ? <p className={styles.saleText}>{saleOutcomeCopy}</p> : null}
            </div>

            <aside className={styles.heroAside} aria-label="Catalog access guidance">
              <div className={styles.metricCard}>
                <p className={styles.metricLabel}>Access state</p>
                <p className={styles.metricValue}>{accessRemediation?.heading}</p>
                <p className={styles.metricText}>{accessRemediation?.body}</p>
                <p className={styles.metricDetail}>{accessRemediation?.detail}</p>
                {accessRemediation ? (
                  <Link to={accessRemediation.action.to} className={styles.metricActionLink}>
                    {accessRemediation.action.label}
                  </Link>
                ) : null}
              </div>
              <div className={styles.metricCard}>
                <p className={styles.metricLabel}>Cookbook scale</p>
                <p className={styles.metricValue}>{item.recipe_count} recipes</p>
                <p className={styles.metricText}>
                  Use this detail view to understand the cookbook lane before you step back into dinner planning.
                </p>
                {item.publication?.publication_status === 'published' ? (
                  <p className={styles.metricDetail}>
                    Published for sale at ${(item.publication.list_price_cents / 100).toFixed(2)} {item.publication.currency.toUpperCase()}.
                  </p>
                ) : null}
              </div>
            </aside>
          </header>

          <div className={styles.actionsBar}>
            <Link to={catalogPathway.to} className={styles.secondaryLink}>
              Back to catalog
            </Link>
            <Button
              type="button"
              onClick={() =>
                navigate(plannerPathway.to, {
                  state: plannerHandoff
                    ? {
                        plannerCatalogCookbook: plannerHandoff,
                      }
                    : undefined,
                })
              }
            >
              {plannerHandoff && item ? getPlannerLinkLabel(item) : 'Return to dinner planner'}
            </Button>
          </div>

          <section className={styles.contentGrid}>
            <article className={styles.panel}>
              <p className={styles.sectionEyebrow}>Sample recipes</p>
              <h2 className={styles.sectionTitle}>What this cookbook includes</h2>
              {item.sample_recipe_titles.length > 0 ? (
                <ul className={styles.recipeList}>
                  {item.sample_recipe_titles.map((recipeTitle) => (
                    <li key={recipeTitle} className={styles.recipeListItem}>
                      {recipeTitle}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.sectionText}>Sample recipes are not available for this cookbook yet.</p>
              )}
            </article>

            <article className={styles.panel}>
              <p className={styles.sectionEyebrow}>Tags</p>
              <h2 className={styles.sectionTitle}>Catalog signals</h2>
              {item.tags.length > 0 ? (
                <div className={styles.tagList}>
                  {item.tags.map((tag) => (
                    <span key={tag} className={styles.tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : (
                <p className={styles.sectionText}>No editorial tags are attached to this cookbook yet.</p>
              )}
            </article>
          </section>
        </>
      ) : null}
    </div>
  );
}
