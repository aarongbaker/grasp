import { apiFetch } from './client';
import type {
  CatalogCookbookDetailResponse,
  CatalogCookbookListResponse,
  MarketplaceCookbookPublicationSummary,
  MarketplacePublicationListResponse,
  MarketplacePublicationUpsertRequest,
} from '../types/api';

export function listCatalogCookbooks(): Promise<CatalogCookbookListResponse> {
  return apiFetch<CatalogCookbookListResponse>('/catalog/cookbooks');
}

export function getCatalogCookbook(catalogCookbookId: string): Promise<CatalogCookbookDetailResponse> {
  return apiFetch<CatalogCookbookDetailResponse>(`/catalog/cookbooks/${catalogCookbookId}`);
}

export function listMarketplacePublications(): Promise<MarketplacePublicationListResponse> {
  return apiFetch<MarketplacePublicationListResponse>('/catalog/cookbooks/marketplace/publications');
}

export function upsertMarketplacePublication(
  body: MarketplacePublicationUpsertRequest,
): Promise<MarketplaceCookbookPublicationSummary> {
  return apiFetch<MarketplaceCookbookPublicationSummary>('/catalog/cookbooks/marketplace/publications', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
