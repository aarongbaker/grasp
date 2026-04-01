import { apiFetch } from './client';
import type { RecipeCookbookCreateRequest, RecipeCookbookDetail } from '../types/api';

export function listRecipeCookbooks(): Promise<RecipeCookbookDetail[]> {
  return apiFetch<RecipeCookbookDetail[]>('/recipe-cookbooks');
}

export function createRecipeCookbook(body: RecipeCookbookCreateRequest): Promise<RecipeCookbookDetail> {
  return apiFetch<RecipeCookbookDetail>('/recipe-cookbooks', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
