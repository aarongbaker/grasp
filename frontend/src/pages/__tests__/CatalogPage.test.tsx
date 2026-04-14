import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as catalogApi from '../../api/catalog';
import { CatalogPage } from '../CatalogPage';
import type { CatalogCookbookListResponse } from '../../types/api';

function buildListResponse(): CatalogCookbookListResponse {
  return {
    items: [
      {
        catalog_cookbook_id: 'catalog-1',
        slug: 'weeknight-foundations',
        title: 'Weeknight Foundations',
        subtitle: 'Fast, dependable dinner collections',
        cover_image_url: null,
        recipe_count: 18,
        audience: 'included',
        access_state: 'included',
        access_state_reason: 'Included with your current catalog access.',
        access_diagnostics: null,
      },
      {
        catalog_cookbook_id: 'catalog-2',
        slug: 'spring-market-preview',
        title: 'Spring Market Preview',
        subtitle: 'Editorial picks from the market table',
        cover_image_url: 'https://images.example.com/spring.jpg',
        recipe_count: 9,
        audience: 'preview',
        access_state: 'preview',
        access_state_reason: 'Preview recipes are open so you can evaluate the collection before unlocking more.',
        access_diagnostics: null,
      },
      {
        catalog_cookbook_id: 'catalog-3',
        slug: 'chef-reserve',
        title: 'Chef Reserve',
        subtitle: null,
        cover_image_url: null,
        recipe_count: 24,
        audience: 'premium',
        access_state: 'locked',
        access_state_reason: 'Upgrade access is required before this cookbook can be used in planning.',
        access_diagnostics: {
          subscription_snapshot_id: 'snapshot-2',
          subscription_status: 'past_due',
          sync_state: 'stale',
          provider: 'stripe',
        },
      },
    ],
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <CatalogPage />
    </MemoryRouter>,
  );
}

describe('CatalogPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders catalog cards from API access states without deriving them from audience', async () => {
    vi.spyOn(catalogApi, 'listCatalogCookbooks').mockResolvedValue(buildListResponse());

    renderPage();

    expect(screen.getByLabelText('Loading cookbook catalog')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Spring Market Preview' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Chef Reserve' })).toBeInTheDocument();

    expect(screen.getByText('Included')).toBeInTheDocument();
    expect(screen.getByText('Preview')).toBeInTheDocument();
    expect(screen.getByText('Locked')).toBeInTheDocument();

    expect(screen.getByText('Included with your current catalog access.')).toBeInTheDocument();
    expect(
      screen.getByText('Preview recipes are open so you can evaluate the collection before unlocking more.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Upgrade access is required before this cookbook can be used in planning.')).toBeInTheDocument();

    const detailLinks = screen.getAllByRole('link', { name: 'View cookbook details' });
    expect(detailLinks).toHaveLength(3);
    expect(detailLinks[0]).toHaveAttribute('href', '/catalog/catalog-1');
    expect(screen.getByRole('link', { name: /Open dinner planner/i })).toHaveAttribute('href', '/sessions/new');
    expect(screen.queryByText(/folder|shelf/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/detected[- ]recipes?/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/recovered from the cookbook collection/i)).not.toBeInTheDocument();
  });

  it('accepts diagnostics-bearing catalog payloads without rendering billing inference in the browse UI', async () => {
    vi.spyOn(catalogApi, 'listCatalogCookbooks').mockResolvedValue(buildListResponse());

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
    expect(screen.queryByText(/stripe|active|past_due|synced|stale|snapshot/i)).not.toBeInTheDocument();
  });

  it('renders an explicit empty state when the catalog response has no items', async () => {
    vi.spyOn(catalogApi, 'listCatalogCookbooks').mockResolvedValue({ items: [] });

    renderPage();

    expect(await screen.findByRole('heading', { name: 'No platform cookbooks are available yet.' })).toBeInTheDocument();
    expect(screen.getByText(/When the catalog feed is empty, this lane stays visible/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refresh catalog' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Return to dinner planner' })).toHaveAttribute('href', '/sessions/new');
  });

  it('surfaces retryable error copy when the list request fails', async () => {
    const user = userEvent.setup();
    const listSpy = vi
      .spyOn(catalogApi, 'listCatalogCookbooks')
      .mockRejectedValueOnce(new Error('Catalog unavailable'))
      .mockResolvedValueOnce(buildListResponse());

    renderPage();

    expect(await screen.findByRole('heading', { name: 'The cookbook catalog did not load.' })).toBeInTheDocument();
    expect(screen.getAllByText('Catalog unavailable')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() => expect(listSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
  });

  it('treats malformed list payloads as fetch failures instead of inventing client fallbacks', async () => {
    vi.spyOn(catalogApi, 'listCatalogCookbooks').mockResolvedValue({
      items: [{ title: 'Broken payload' }],
    } as unknown as CatalogCookbookListResponse);

    renderPage();

    expect(await screen.findByRole('heading', { name: 'The cookbook catalog did not load.' })).toBeInTheDocument();
    expect(screen.getByText('Catalog data came back in an unexpected shape. Please retry in a moment.')).toBeInTheDocument();
  });
});
