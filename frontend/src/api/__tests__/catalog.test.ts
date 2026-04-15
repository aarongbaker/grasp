import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getCatalogCookbook, listCatalogCookbooks } from '../catalog';
import type {
  CatalogCookbookAccessState,
  CatalogCookbookAudience,
  CatalogCookbookDetailResponse,
  CatalogCookbookListResponse,
} from '../../types/api';

const fetchMock = vi.fn<typeof fetch>();

function createStorageMock(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

function buildListResponse(): CatalogCookbookListResponse {
  return {
    items: [
      {
        catalog_cookbook_id: '11111111-1111-1111-1111-111111111111',
        slug: 'weeknight-foundations',
        title: 'Weeknight Foundations',
        subtitle: 'Fast, dependable platform-managed dinner ideas',
        cover_image_url: null,
        recipe_count: 18,
        audience: 'included' satisfies CatalogCookbookAudience,
        access_state: 'included' satisfies CatalogCookbookAccessState,
        access_state_reason: 'Included with the base catalog',
        ownership: {
          is_owned: false,
          ownership_source: null,
          access_reason: null,
        },
        access_diagnostics: null,
      },
    ],
  };
}

function buildDetailResponse(): CatalogCookbookDetailResponse {
  return {
    item: {
      ...buildListResponse().items[0],
      description: 'Platform-managed reference collection for dependable dinners with minimal lead time.',
      sample_recipe_titles: ['Skillet Chicken Piccata', 'Tomato Braised Chickpeas'],
      tags: ['weeknight', 'foundations'],
    },
  };
}

describe('catalog api client', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('localStorage', createStorageMock());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('lists catalog cookbooks from the dedicated read-only catalog endpoint', async () => {
    const payload = buildListResponse();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(listCatalogCookbooks()).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/catalog/cookbooks',
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    );
  });

  it('gets one catalog cookbook detail from the catalog seam without using recipe cookbook ids', async () => {
    const payload = buildDetailResponse();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

    await expect(getCatalogCookbook(payload.item.catalog_cookbook_id)).resolves.toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/catalog/cookbooks/${payload.item.catalog_cookbook_id}`,
      expect.objectContaining({
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    );
  });
});
