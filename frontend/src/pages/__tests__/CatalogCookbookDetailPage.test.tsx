import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as catalogApi from '../../api/catalog';
import { CatalogCookbookDetailPage } from '../CatalogCookbookDetailPage';
import type { CatalogCookbookDetailResponse } from '../../types/api';

function buildDetailResponse(
  accessState: 'included' | 'preview' | 'locked' = 'included',
  diagnostics?: CatalogCookbookDetailResponse['item']['access_diagnostics'],
): CatalogCookbookDetailResponse {
  const reasons = {
    included: 'Included with your current catalog access.',
    preview: 'Preview recipes are open so you can evaluate the collection before unlocking more.',
    locked: 'Upgrade access is required before this cookbook can be used in planning.',
  } as const;

  return {
    item: {
      catalog_cookbook_id: 'catalog-1',
      slug: 'weeknight-foundations',
      title: 'Weeknight Foundations',
      subtitle: 'Fast, dependable dinner collections',
      cover_image_url: null,
      recipe_count: 18,
      audience: accessState === 'locked' ? 'premium' : accessState,
      access_state: accessState,
      access_state_reason: reasons[accessState],
      access_diagnostics: diagnostics ?? {
        subscription_snapshot_id: accessState === 'locked' ? 'snapshot-locked' : 'snapshot-open',
        subscription_status: accessState === 'locked' ? 'past_due' : 'active',
        sync_state: accessState === 'locked' ? 'stale' : 'synced',
        provider: 'stripe',
      },
      description: 'A dependable platform-managed collection for fast service nights and low-friction menu building.',
      sample_recipe_titles: ['Skillet Chicken Piccata', 'Tomato Braised Chickpeas'],
      tags: ['weeknight', 'foundations'],
    },
  };
}

function renderPage(path = '/catalog/catalog-1') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/catalog/:catalogCookbookId" element={<CatalogCookbookDetailPage />} />
        <Route path="/catalog" element={<div>Catalog listing</div>} />
        <Route path="/profile" element={<div>Profile page</div>} />
        <Route path="/sessions/new" element={<div data-testid="planner-state">planner route</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('CatalogCookbookDetailPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders cookbook detail content and planner handoff from the API contract', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(buildDetailResponse('included'));

    renderPage();

    expect(screen.getByLabelText('Loading cookbook detail')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
    expect(screen.getByText('Included')).toBeInTheDocument();
    expect(screen.getByText('Included in your catalog lane')).toBeInTheDocument();
    expect(screen.getByText('Included with your current catalog access.')).toBeInTheDocument();
    expect(screen.getByText('You can hand this cookbook off to dinner planning immediately from here.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Plan from this cookbook' })).toHaveAttribute('href', '/sessions/new');
    expect(screen.getByText('Skillet Chicken Piccata')).toBeInTheDocument();
    expect(screen.getByText('Tomato Braised Chickpeas')).toBeInTheDocument();
    expect(screen.getByText('weeknight')).toBeInTheDocument();
    expect(screen.getByText('foundations')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to catalog' })).toHaveAttribute('href', '/catalog');
    expect(screen.getByRole('button', { name: 'Plan from included catalog cookbook' })).toBeInTheDocument();
  });

  it('passes a stable planner catalog handoff into /sessions/new from the detail action', async () => {
    const user = userEvent.setup();
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(buildDetailResponse('preview'));

    renderPage();

    await screen.findByRole('heading', { name: 'Weeknight Foundations' });
    await user.click(screen.getByRole('button', { name: 'Plan from preview catalog cookbook' }));

    expect(await screen.findByTestId('planner-state')).toBeInTheDocument();
  });

  it('accepts diagnostics-bearing detail payloads without surfacing provider or billing state in detail copy', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(buildDetailResponse('preview'));

    renderPage();

    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
    expect(screen.queryByText(/stripe|active|past_due|synced|stale|snapshot/i)).not.toBeInTheDocument();
  });

  it('renders preview guidance with account-access follow-up while keeping preview planning available', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(buildDetailResponse('preview'));

    renderPage();

    expect(await screen.findByText('Preview access is available')).toBeInTheDocument();
    expect(screen.getByText('Preview recipes are open so you can evaluate the collection before unlocking more.')).toBeInTheDocument();
    expect(
      screen.getByText(/Preview cookbooks can still seed the planner, so you can test the lane before deciding whether to unlock more access\./i),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Plan from preview access' })).toHaveAttribute('href', '/sessions/new');
  });

  it('shows locked upgrade remediation that routes to profile instead of exposing provider detail', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(buildDetailResponse('locked'));

    renderPage('/catalog/catalog-locked');

    expect(await screen.findByText('This cookbook is currently locked')).toBeInTheDocument();
    expect(screen.getByText('Upgrade access is required before this cookbook can be used in planning.')).toBeInTheDocument();
    expect(screen.getByText(/If you still need this cookbook in the planner, review your account access first\./i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review account access' })).toHaveAttribute('href', '/profile');
    expect(screen.getByText('Locked')).toBeInTheDocument();
  });

  it('shows sync-refresh remediation when diagnostics report failed refresh on a locked cookbook', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue(
      buildDetailResponse('locked', {
        subscription_snapshot_id: 'snapshot-sync',
        subscription_status: 'cancelled',
        sync_state: 'failed',
        provider: 'stripe',
      }),
    );

    renderPage('/catalog/catalog-sync');

    expect(await screen.findByText('Catalog access is waiting on a refresh')).toBeInTheDocument();
    expect(screen.getByText(/Your account access needs to finish refreshing before this cookbook can move into planning\./i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Review account access' })).toHaveAttribute('href', '/profile');
    expect(screen.queryByText(/stripe|cancelled|failed|snapshot/i)).not.toBeInTheDocument();
  });

  it('shows a recoverable error state for rejected detail requests and lets the user retry', async () => {
    const user = userEvent.setup();
    const getSpy = vi
      .spyOn(catalogApi, 'getCatalogCookbook')
      .mockRejectedValueOnce(new Error('Catalog detail unavailable'))
      .mockResolvedValueOnce(buildDetailResponse('included'));

    renderPage();

    expect(await screen.findByRole('heading', { name: 'This cookbook detail could not be shown.' })).toBeInTheDocument();
    expect(screen.getAllByText('Catalog detail unavailable')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: 'Try again' }));

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('heading', { name: 'Weeknight Foundations' })).toBeInTheDocument();
  });

  it('treats malformed detail payloads as failures instead of inventing local access logic', async () => {
    vi.spyOn(catalogApi, 'getCatalogCookbook').mockResolvedValue({ item: { title: 'Broken payload' } } as unknown as CatalogCookbookDetailResponse);

    renderPage();

    expect(await screen.findByRole('heading', { name: 'This cookbook detail could not be shown.' })).toBeInTheDocument();
    expect(screen.getByText('Catalog cookbook detail came back in an unexpected shape. Please retry in a moment.')).toBeInTheDocument();
  });

  it('rejects missing route params through the detail failure path instead of crashing', async () => {
    render(
      <MemoryRouter initialEntries={['/catalog/']}>
        <Routes>
          <Route path="/catalog/" element={<CatalogCookbookDetailPage />} />
          <Route path="/catalog" element={<div>Catalog listing</div>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('heading', { name: 'This cookbook detail could not be shown.' })).toBeInTheDocument();
    expect(screen.getByText('Choose a catalog cookbook from the browse page before opening details.')).toBeInTheDocument();
  });
});
