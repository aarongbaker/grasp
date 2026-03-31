import type { DetectedRecipeCandidate } from '../../types/api';
import { getRecipeDisplayTitle } from '../../utils/cookbookTitles';

export interface CookbookCandidatePreview {
  title: string;
  subtitle: string;
  ingredients: string[];
  steps: string[];
  notes: string[];
  excerpt: string;
}

const HEADING_RE = /^(ingredients?|method|directions?|steps?|preparation|notes?)[:\s]*$/i;
const METHOD_START_RE = /^(?:\d+[.)]|[-*•])\s+|^(?:add|bake|beat|boil|brown|brush|chill|combine|cook|cover|cut|drain|drop|fold|fry|garnish|heat|knead|let|line|melt|mix|peel|place|pour|reduce|remove|roll|roast|season|serve|set|shape|simmer|slice|soak|sprinkle|stir|strain|turn|wash|whip)\b/i;
const INGREDIENT_START_RE = /^(?:\d|[%¼½¾⅓⅔⅛⅜⅝⅞]|[A-Za-z]\)|[A-Za-z]+\s*\d|salt\b|pepper\b|paprika\b|parsley\b|flour\b|butter\b|sugar\b)/i;

function normaliseLines(text: string): string[] {
  return text
    .split(/\n+/)
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
}

function cleanLine(line: string): string {
  return line.replace(/^(?:\d+[.)]|[-*•])\s*/, '').replace(/\s+/g, ' ').trim();
}

function buildExcerpt(lines: string[], displayTitle: string, subtitle: string): string {
  const normalizedTitle = displayTitle.trim().toLowerCase();
  const normalizedSubtitle = subtitle.trim().toLowerCase();

  const excerptLines = lines.filter((line) => {
    const normalizedLine = line.trim().toLowerCase();
    if (!normalizedLine) return false;
    if (normalizedTitle && normalizedLine === normalizedTitle) return false;
    if (normalizedSubtitle && normalizedLine === normalizedSubtitle) return false;
    return true;
  });

  return excerptLines.slice(0, 3).join(' ');
}

export function buildCookbookCandidatePreview(recipe: DetectedRecipeCandidate): CookbookCandidatePreview {
  const lines = normaliseLines(recipe.text || '');
  const ingredients: string[] = [];
  const steps: string[] = [];
  const notes: string[] = [];

  let section: 'lead' | 'ingredients' | 'method' | 'notes' = 'lead';

  for (const rawLine of lines) {
    const line = cleanLine(rawLine);
    if (!line) continue;

    if (HEADING_RE.test(line)) {
      const lowered = line.toLowerCase();
      if (lowered.startsWith('ingredient')) section = 'ingredients';
      else if (lowered.startsWith('method') || lowered.startsWith('direction') || lowered.startsWith('step') || lowered.startsWith('preparation')) section = 'method';
      else section = 'notes';
      continue;
    }

    if (section === 'lead') {
      if (INGREDIENT_START_RE.test(line)) {
        section = 'ingredients';
      } else if (METHOD_START_RE.test(line)) {
        section = 'method';
      }
    }

    if (section === 'ingredients') {
      if (METHOD_START_RE.test(line) && ingredients.length > 0) {
        section = 'method';
      }
    }

    if (section === 'method') {
      steps.push(line);
      continue;
    }

    if (section === 'ingredients') {
      ingredients.push(line);
      continue;
    }

    notes.push(line);
  }

  const title = getRecipeDisplayTitle(recipe);
  const subtitle = recipe.chapter?.trim() || recipe.book_title;
  const fallbackExcerpt = buildExcerpt(lines, title, subtitle);

  return {
    title,
    subtitle,
    ingredients: ingredients.slice(0, 8),
    steps: steps.slice(0, 4),
    notes: notes.slice(0, 2),
    excerpt: fallbackExcerpt,
  };
}
