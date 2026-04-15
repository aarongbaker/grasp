import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listCatalogCookbooks } from '../api/catalog';
import { pathwayByKey } from '../components/layout/pathways';
import { Button } from '../components/shared/Button';
import { Skeleton } from '../components/shared/Skeleton';
import type {
  CatalogAccessDiagnostics,
  CatalogCookbookOwnershipStatus,
  CatalogCookbookSummary,
} from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './CatalogPage.module.css';

type CatalogStatus = 'loading' | 'ready' | 'error';

const plannerPathway = pathwayByKey['generated-planner'];

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

function isCatalogCookbookSummary(value: unknown): value is CatalogCookbookSummary {
  if (!value || typeof value !== 'object') {
    return false;
  }

  const item = value as Partial<CatalogCookbookSummary>;
  return (
    typeof item.catalog_cookbook_id === 'string' &&
    item.catalog_cookbook_id.length > 0 &&
    typeof item.slug === 'string' &&
    typeof item.title === 'string' &&
    typeof item.recipe_count === 'number' &&
    typeof item.access_state === 'string' &&
    typeof item.access_state_reason === 'string' &&
    isCatalogCookbookOwnershipStatus(item.ownership) &&
    isCatalogAccessDiagnostics(item.access_diagnostics)
  );
}

function getAccessBadgeLabel(summary: CatalogCookbookSummary): string {
  if (summary.ownership.is_owned) {
    return 'Owned';
  }

  switch (summary.access_state) {
    case 'included':
      return 'Included';
    case 'preview':
      return 'Preview';
    case 'locked':
      return 'Locked';
    default:
      return summary.access_state;
  }
}

function getAccessBadgeClass(summary: CatalogCookbookSummary): string {
  return summary.ownership.is_owned ? styles.access_owned : styles[`access_${summary.access_state}`];
}

function getOwnershipCopy(summary: CatalogCookbookSummary): string | null {
  if (!summary.ownership.is_owned) {
    return null;
  }

  return summary.ownership.access_reason ?? 'You already own this platform cookbook, so access stays available even if your subscription changes later.';
}

function getCatalogActionCopy(summary: CatalogCookbookSummary): string {
  if (summary.ownership.is_owned) {
    return 'Open your owned cookbook';
  }
  if (summary.access_state === 'locked') {
    return 'Review access options';
  }
  if (summary.access_state === 'preview') {
    return 'Open preview cookbook';
  }
  return 'View cookbook details';
}

export function CatalogPage() {
  const [status, setStatus] = useState<CatalogStatus>('loading');
  const [items, setItems] = useState<CatalogCookbookSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const fetchCatalog = useCallback(async () => {
    setStatus('loading');
    setError(null);

    try {
      const response = await listCatalogCookbooks();
      if (!response || !Array.isArray(response.items) || !response.items.every(isCatalogCookbookSummary)) {
        throw new Error('Catalog data came back in an unexpected shape. Please retry in a moment.');
      }

      setItems(response.items);
      setStatus('ready');
    } catch (err) {
      setItems([]);
      setError(getErrorMessage(err, 'Could not load the cookbook catalog.'));
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    void fetchCatalog();
  }, [fetchCatalog]);

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>Platform catalog</p>
          <h1 className={styles.title}>Browse Cookbook Catalog</h1>
          <p className={styles.subtitle}>
            Explore featured cookbook collections, trust the platform&apos;s included, preview, locked, and owned access states as-is,
            and step back into dinner planning once you know which lane you want to cook from.
          </p>
        </div>

        <aside className={styles.heroAside} aria-label="Catalog health">
          <div className={styles.metricCard}>
            <p className={styles.metricLabel}>Catalog state</p>
            <p className={styles.metricValue}>
              {status === 'loading' ? 'Loading catalog…' : status === 'error' ? 'Catalog unavailable' : 'Catalog ready'}
            </p>
            <p className={styles.metricText}>
              {status === 'loading'
                ? 'Waiting on the read-only catalog feed before any cookbook cards render.'
                : status === 'error'
                  ? 'A fetch or response-shape problem blocked the browse surface before access states could render.'
                  : 'Access badges, ownership guidance, and reasons below are rendered from the API payload, not recomputed in the browser.'}
            </p>
          </div>

          <div className={styles.metricCard}>
            <p className={styles.metricLabel}>Planner handoff</p>
            <p className={styles.metricValue}>Discover here. Plan there.</p>
            <p className={styles.metricText}>
              Stay in the catalog while you compare cookbook lanes, then return to the dinner planner when you are ready to
              turn one of those lanes into service timing.
            </p>
            <Link to={plannerPathway.to} className={styles.ctaLink}>
              {plannerPathway.cta}
            </Link>
          </div>
        </aside>
      </header>

      {status === 'loading' ? (
        <section className={styles.loadingState} aria-label="Loading cookbook catalog">
          <Skeleton variant="card" count={3} />
        </section>
      ) : status === 'error' ? (
        <section className={styles.errorState} aria-live="polite">
          <p className={styles.sectionEyebrow}>Catalog fetch failed</p>
          <h2 className={styles.sectionTitle}>The cookbook catalog did not load.</h2>
          <p className={styles.sectionText}>{error ?? 'Could not load the cookbook catalog.'}</p>
          <div className={styles.actionRow}>
            <Button variant="secondary" onClick={() => void fetchCatalog()}>
              Try again
            </Button>
            <Link to={plannerPathway.to} className={styles.inlineLink}>
              Return to dinner planner
            </Link>
          </div>
        </section>
      ) : items.length === 0 ? (
        <section className={styles.emptyState} aria-live="polite">
          <p className={styles.sectionEyebrow}>Catalog is empty</p>
          <h2 className={styles.sectionTitle}>No platform cookbooks are available yet.</h2>
          <p className={styles.sectionText}>
            When the catalog feed is empty, this lane stays visible so you can retry later or head back to the dinner planner.
          </p>
          <div className={styles.actionRow}>
            <Button variant="secondary" onClick={() => void fetchCatalog()}>
              Refresh catalog
            </Button>
            <Link to={plannerPathway.to} className={styles.inlineLink}>
              Return to dinner planner
            </Link>
          </div>
        </section>
      ) : (
        <section className={styles.catalogGrid} aria-label="Cookbook catalog results">
          {items.map((item) => {
            const ownershipCopy = getOwnershipCopy(item);
            return (
              <article key={item.catalog_cookbook_id} className={styles.catalogCard}>
                {item.cover_image_url ? (
                  <img
                    src={item.cover_image_url}
                    alt={`Cover for ${item.title}`}
                    className={styles.coverImage}
                  />
                ) : (
                  <div className={styles.coverFallback} aria-hidden="true">
                    <span>{item.title.slice(0, 1).toUpperCase()}</span>
                  </div>
                )}

                <div className={styles.cardBody}>
                  <div className={styles.cardHeader}>
                    <div>
                      <p className={styles.cardEyebrow}>Cookbook collection</p>
                      <h2 className={styles.cardTitle}>{item.title}</h2>
                      {item.subtitle ? <p className={styles.cardSubtitle}>{item.subtitle}</p> : null}
                    </div>
                    <span className={`${styles.accessBadge} ${getAccessBadgeClass(item)}`}>
                      {getAccessBadgeLabel(item)}
                    </span>
                  </div>

                  <p className={styles.cardMeta}>{item.recipe_count} recipes</p>
                  <p className={styles.reasonText}>{item.access_state_reason}</p>
                  {ownershipCopy ? <p className={styles.ownershipText}>{ownershipCopy}</p> : null}

                  <div className={styles.cardActions}>
                    <Link to={`/catalog/${item.catalog_cookbook_id}`} className={styles.detailLink}>
                      {getCatalogActionCopy(item)}
                    </Link>
                  </div>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
