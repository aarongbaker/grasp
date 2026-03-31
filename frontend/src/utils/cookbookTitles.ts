import type { DetectedRecipeCandidate } from '../types/api';

function cleanText(value: string | null | undefined): string {
  return value?.replace(/\s+/g, ' ').trim() ?? '';
}

export function getRecipeDisplayTitle(recipe: DetectedRecipeCandidate): string {
  const recipeName = cleanText(recipe.recipe_name);
  if (recipeName) {
    return recipeName;
  }

  const chapter = cleanText(recipe.chapter);
  const page = recipe.page_number;

  if (chapter && page) {
    return `${chapter}, p. ${page}`;
  }
  if (chapter) {
    return chapter;
  }
  if (page) {
    return `Recipe on page ${page}`;
  }

  return 'Untitled Recipe';
}
