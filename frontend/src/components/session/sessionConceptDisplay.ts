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

export function getSessionConceptDisplay(concept: DinnerConcept): SessionConceptDisplayModel {
  const authoredTitle = getAuthoredTitle(concept);
  const freeText = cleanText(concept.free_text);
  const title = authoredTitle ?? freeText ?? 'Dinner session';

  if (concept.concept_source === 'authored') {
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

  const generatedPlanner = pathwayByKey['generated-planner'];
  const conceptSource = concept.concept_source ?? 'free_text';
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
