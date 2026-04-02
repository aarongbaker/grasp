import { pathwayByKey } from '../layout/pathways';
import type { DinnerConcept } from '../../types/api';

export interface SessionConceptDisplayModel {
  title: string;
  pathwayKey: 'generated-planner' | 'recipe-library' | 'authored-workspace';
  pathwayLabel: string;
  sourceLabel: string;
  sourceDetail: string;
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

export function getSessionConceptDisplay(concept: DinnerConcept): SessionConceptDisplayModel {
  const conceptSource = concept.concept_source ?? 'free_text';
  const authoredTitle = getAuthoredTitle(concept);
  const plannerAuthoredTitle = getPlannerAuthoredAnchorTitle(concept);
  const plannerCookbookTitle = getPlannerCookbookTargetTitle(concept);
  const freeText = cleanText(concept.free_text);
  const title = authoredTitle ?? plannerAuthoredTitle ?? plannerCookbookTitle ?? freeText ?? 'Dinner session';

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
