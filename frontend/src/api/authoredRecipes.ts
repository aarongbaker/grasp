import { apiFetch } from './client';
import type {
  AuthoredRecipeCookbookUpdateRequest,
  AuthoredRecipeCreateRequest,
  AuthoredRecipeDetail,
  AuthoredRecipeListItem,
} from '../types/api';

export function createAuthoredRecipe(body: AuthoredRecipeCreateRequest): Promise<AuthoredRecipeDetail> {
  return apiFetch<AuthoredRecipeDetail>('/authored-recipes', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function listAuthoredRecipes(): Promise<AuthoredRecipeListItem[]> {
  return apiFetch<AuthoredRecipeListItem[]>('/authored-recipes');
}

export function getAuthoredRecipe(recipeId: string): Promise<AuthoredRecipeDetail> {
  return apiFetch<AuthoredRecipeDetail>(`/authored-recipes/${recipeId}`);
}

export function updateAuthoredRecipeCookbook(
  recipeId: string,
  body: AuthoredRecipeCookbookUpdateRequest,
): Promise<AuthoredRecipeDetail> {
  return apiFetch<AuthoredRecipeDetail>(`/authored-recipes/${recipeId}/cookbook`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}
