import type { DinnerConcept, SelectedCookbookRecipe } from '../../types/api';

export interface SessionConceptDisplayModel {
  isCookbook: boolean;
  title: string;
  sourceLabel: string;
  sourceDetail: string | null;
  recipeSummary: string | null;
  recipeCount: number;
  recipeNames: string[];
  cookbookTitles: string[];
}

function cleanText(value: string | null | undefined): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function recipeNameFromSelection(recipe: SelectedCookbookRecipe): string | null {
  const explicitName = cleanText((recipe as SelectedCookbookRecipe & { recipe_name?: string }).recipe_name);
  if (explicitName) return explicitName;

  const text = cleanText(recipe.text);
  if (!text) return null;

  const [firstLine] = text.split(/\r?\n/, 1);
  return cleanText(firstLine);
}

function uniqueNonEmpty(values: Array<string | null>): string[] {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value))));
}

function formatList(values: string[]): string {
  if (values.length === 0) return '';
  if (values.length === 1) return values[0];
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(', ')}, and ${values[values.length - 1]}`;
}

function summarizeRecipes(recipeNames: string[]): string | null {
  if (recipeNames.length === 0) return null;
  if (recipeNames.length <= 3) return formatList(recipeNames);
  return `${recipeNames.slice(0, 3).join(', ')}, +${recipeNames.length - 3} more`;
}

export function getSessionConceptDisplay(concept: DinnerConcept): SessionConceptDisplayModel {
  const fallbackTitle = cleanText(concept.free_text) ?? 'Dinner session';
  const selectedRecipes = concept.selected_recipes ?? [];
  const recipeNames = uniqueNonEmpty(selectedRecipes.map(recipeNameFromSelection));
  const cookbookTitles = uniqueNonEmpty(selectedRecipes.map((recipe) => cleanText(recipe.book_title)));
  const recipeSummary = summarizeRecipes(recipeNames);
  const isCookbook = concept.concept_source === 'cookbook';

  if (!isCookbook) {
    return {
      isCookbook: false,
      title: fallbackTitle,
      sourceLabel: 'Meal idea',
      sourceDetail: null,
      recipeSummary: null,
      recipeCount: 0,
      recipeNames: [],
      cookbookTitles: [],
    };
  }

  const cookbookDetail = cookbookTitles.length > 0
    ? cookbookTitles.length === 1
      ? `From ${cookbookTitles[0]}`
      : `From ${cookbookTitles.length} cookbooks`
    : 'Cookbook-selected session';

  return {
    isCookbook: true,
    title: recipeSummary ? `Cookbook menu: ${recipeSummary}` : fallbackTitle,
    sourceLabel: 'Cookbook menu',
    sourceDetail: cookbookDetail,
    recipeSummary: recipeSummary ?? fallbackTitle,
    recipeCount: selectedRecipes.length,
    recipeNames,
    cookbookTitles,
  };
}
