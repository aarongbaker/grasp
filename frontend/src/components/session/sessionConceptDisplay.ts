import { pathwayByKey } from '../layout/pathways';
import type { PathwayKey } from '../layout/pathways';
import type { DinnerConcept, RecipeProvenance, ValidatedRecipe } from '../../types/api';

export interface SessionConceptDisplayModel {
  title: string;
  pathwayKey: PathwayKey;
  pathwayLabel: string;
  sourceLabel: string;
  sourceDetail: string;
}

export interface RecipeProvenanceDisplayModel {
  label: string;
  detail: string;
}

function cleanText(value: string | null | undefined): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function getAuthoredTitle(concept: DinnerConcept): string | null {
  return cleanText(concept.selected_authored_recipe?.title);
}

function getPlannerAuthoredAnchorTitle(concept: DinnerConcept): string | null {
  return cleanText(concept.planner_authored_recipe_anchor?.title);
}

function getPlannerCookbookTargetTitle(concept: DinnerConcept): string | null {
  return cleanText(concept.planner_cookbook_target?.name);
}

function getPlannerCatalogCookbookTitle(concept: DinnerConcept): string | null {
  return cleanText(concept.planner_catalog_cookbook?.title);
}

function getProvenanceLabel(kind: RecipeProvenance['kind']): string {
  switch (kind) {
    case 'library_authored':
      return 'From your recipe library';
    case 'library_cookbook':
      return 'From your cookbook library';
    case 'generated':
    default:
      return 'Generated for this session';
  }
}

export function getRecipeProvenanceDisplay(provenance: RecipeProvenance): RecipeProvenanceDisplayModel {
  const sourceLabel = cleanText(provenance.source_label);

  if (provenance.kind === 'library_authored') {
    return {
      label: getProvenanceLabel(provenance.kind),
      detail: sourceLabel
        ? `Anchored to your saved recipe “${sourceLabel}”.`
        : 'Anchored to a saved recipe from your private library.',
    };
  }

  if (provenance.kind === 'library_cookbook') {
    return {
      label: getProvenanceLabel(provenance.kind),
      detail: sourceLabel
        ? `Shelved in your cookbook folder “${sourceLabel}”.`
        : 'Shelved in one of your cookbook folders.',
    };
  }

  return {
    label: getProvenanceLabel(provenance.kind),
    detail: 'Composed by the planner to complete this service.',
  };
}

export function getValidatedRecipeProvenanceDisplay(recipe: ValidatedRecipe): RecipeProvenanceDisplayModel {
  return getRecipeProvenanceDisplay(recipe.source.source.provenance);
}

export function getSessionConceptDisplay(concept: DinnerConcept): SessionConceptDisplayModel {
  const conceptSource = concept.concept_source ?? 'free_text';
  const authoredTitle = getAuthoredTitle(concept);
  const plannerAuthoredTitle = getPlannerAuthoredAnchorTitle(concept);
  const plannerCookbookTitle = getPlannerCookbookTargetTitle(concept);
  const plannerCatalogCookbookTitle = getPlannerCatalogCookbookTitle(concept);
  const freeText = cleanText(concept.free_text);
  const title = authoredTitle ?? plannerAuthoredTitle ?? plannerCookbookTitle ?? plannerCatalogCookbookTitle ?? freeText ?? 'Dinner session';

  if (conceptSource === 'authored') {
    const recipeLibrary = pathwayByKey['recipe-library'];

    return {
      title,
      pathwayKey: recipeLibrary.key,
      pathwayLabel: recipeLibrary.title,
      sourceLabel: 'Authored recipe',
      sourceDetail: authoredTitle
        ? 'Built from your private library so the session reflects a saved dish rather than a new menu brief.'
        : 'Built from the authored-recipe path. The saved title was missing, so the planning note is shown instead.',
    };
  }

  if (conceptSource === 'planner_authored_anchor') {
    const generatedPlanner = pathwayByKey['generated-planner'];

    return {
      title,
      pathwayKey: generatedPlanner.key,
      pathwayLabel: generatedPlanner.title,
      sourceLabel: 'Planner recipe anchor',
      sourceDetail: plannerAuthoredTitle
        ? 'Built from the dinner planner using one saved recipe as the anchor for a broader service plan.'
        : 'Built from the dinner planner with an authored anchor, but the saved recipe title was missing from the persisted concept.',
    };
  }

  if (conceptSource === 'planner_cookbook_target') {
    const generatedPlanner = pathwayByKey['generated-planner'];

    return {
      title,
      pathwayKey: generatedPlanner.key,
      pathwayLabel: generatedPlanner.title,
      sourceLabel: 'Planner cookbook target',
      sourceDetail: plannerCookbookTitle
        ? 'Built from the dinner planner using one cookbook folder as the planning target.'
        : 'Built from the dinner planner with a cookbook target, but the saved folder name was missing from the persisted concept.',
    };
  }

  if (conceptSource === 'planner_catalog_cookbook') {
    const generatedPlanner = pathwayByKey['generated-planner'];

    return {
      title,
      pathwayKey: generatedPlanner.key,
      pathwayLabel: generatedPlanner.title,
      sourceLabel: 'Planner catalog cookbook',
      sourceDetail: plannerCatalogCookbookTitle
        ? 'Built from the dinner planner using one platform catalog cookbook as the planning seed.'
        : 'Built from the dinner planner with a catalog cookbook, but the trusted catalog title was missing from the persisted concept.',
    };
  }

  const generatedPlanner = pathwayByKey['generated-planner'];
  const sourceDetail = conceptSource === 'cookbook'
    ? 'Built from selected cookbook recipes inside the dinner planner.'
    : 'Built from a fresh dinner brief inside the dinner planner.';

  return {
    title,
    pathwayKey: generatedPlanner.key,
    pathwayLabel: generatedPlanner.title,
    sourceLabel: conceptSource === 'cookbook' ? 'Cookbook selection' : 'Generated plan',
    sourceDetail,
  };
}
