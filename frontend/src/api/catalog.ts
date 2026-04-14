import { apiFetch } from './client';
import type { CatalogCookbookDetailResponse, CatalogCookbookListResponse } from '../types/api';

export function listCatalogCookbooks(): Promise<CatalogCookbookListResponse> {
  return apiFetch<CatalogCookbookListResponse>('/catalog/cookbooks');
}

export function getCatalogCookbook(catalogCookbookId: string): Promise<CatalogCookbookDetailResponse> {
  return apiFetch<CatalogCookbookDetailResponse>(`/catalog/cookbooks/${catalogCookbookId}`);
}
