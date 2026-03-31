import type { DetectedRecipeCandidate } from '../types/api';

const OCR_HEADING_RE = /^(ingredients?|method|directions?|steps?|preparation|notes?)[:\s]*$/i;
const TITLE_BREAK_RE = /^(ingredients?|method|directions?|steps?|preparation|notes?)[:\s]*$/i;
const LEADING_NOISE_RE = /^[\W_]+/;
const TRAILING_PUNCTUATION_RE = /[\s:;,.-]+$/;

function cleanLine(line: string): string {
  return line.replace(/\s+/g, ' ').trim();
}

export function looksLikeOcrNoise(name: string): boolean {
  const trimmed = name.trim();
  if (!trimmed) return true;
  if (trimmed.length < 3) return true;
  if (trimmed.length > 80) return true;

  if (/^[0-9.,;:!?'"—–-]/.test(trimmed)) return true;

  const words = trimmed.split(/\s+/).filter(Boolean);
  const hasMultipleWords = words.length >= 2;
  const startsLowercase = /^[a-z]/.test(trimmed);
  const uppercaseInitials = words.filter((word) => /^[A-Z]/.test(word)).length;

  if (startsLowercase && (!hasMultipleWords || uppercaseInitials === 0)) {
    return true;
  }

  const letters = trimmed.replace(/[^a-zA-Z]/g, '').length;
  const ratio = letters / trimmed.length;
  if (ratio < 0.5) return true;

  if (/[|_\\{}[\]<>~`]+/.test(trimmed)) return true;

  return false;
}

function normalizeTitleCandidate(value: string): string | null {
  const normalized = value
    .replace(LEADING_NOISE_RE, '')
    .replace(TRAILING_PUNCTUATION_RE, '')
    .replace(/\s+/g, ' ')
    .trim();

  if (!normalized) return null;
  if (OCR_HEADING_RE.test(normalized)) return null;
  if (/[.!?]$/.test(value.trim())) return null;

  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length < 2) return null;

  const titleCaseWords = words.filter((word) => /^[A-Z][a-z]/.test(word)).length;
  if (titleCaseWords === 0) return null;

  if (looksLikeOcrNoise(normalized)) return null;
  return normalized;
}

function inferTitleFromChunkText(text: string): string | null {
  const lines = text
    .split(/\r?\n/)
    .map(cleanLine)
    .filter(Boolean);

  for (const line of lines.slice(0, 8)) {
    if (TITLE_BREAK_RE.test(line)) break;

    const candidate = normalizeTitleCandidate(line);
    if (candidate) {
      return candidate;
    }
  }

  return null;
}

export function getRecipeDisplayTitle(recipe: DetectedRecipeCandidate): string {
  const rawName = recipe.recipe_name?.trim() || '';

  if (!looksLikeOcrNoise(rawName)) {
    return rawName;
  }

  const inferredTitle = inferTitleFromChunkText(recipe.text || '');
  if (inferredTitle) {
    return inferredTitle;
  }

  const chapter = recipe.chapter?.trim();
  const page = recipe.page_number;

  if (chapter && page) {
    return `${chapter}, p. ${page}`;
  }
  if (chapter) {
    return chapter;
  }
  if (page) {
    return `Recipe on p. ${page}`;
  }

  return 'Untitled Recipe';
}
