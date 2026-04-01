import type { DinnerConcept } from '../../types/api';

export interface SessionConceptDisplayModel {
  title: string;
}

function cleanText(value: string | null | undefined): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

export function getSessionConceptDisplay(concept: DinnerConcept): SessionConceptDisplayModel {
  const title = cleanText(concept.free_text) ?? 'Dinner session';
  
  return {
    title,
  };
}
