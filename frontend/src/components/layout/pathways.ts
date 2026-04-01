export type PathwayKey = 'generated-planner' | 'recipe-library' | 'authored-workspace';

export type PathwayMeta = {
  key: PathwayKey;
  to: string;
  title: string;
  navLabel: string;
  cta: string;
  purpose: string;
  relationship: string;
  icon: string;
};

export const PATHWAYS: readonly PathwayMeta[] = [
  {
    key: 'generated-planner',
    to: '/sessions/new',
    title: 'Plan a Dinner',
    navLabel: 'Dinner Planner',
    cta: 'Open dinner planner',
    purpose: 'Turn a menu idea into a paced dinner service with timing, equipment flow, and a finished schedule.',
    relationship: 'Use this when service timing leads. Pull in library or authored dishes after the dinner plan takes shape.',
    icon: '+',
  },
  {
    key: 'recipe-library',
    to: '/recipes',
    title: 'Browse Recipe Library',
    navLabel: 'Recipe Library',
    cta: 'Open recipe library',
    purpose: 'Reopen private authored dishes, group them into cookbook folders, and keep your working repertoire within reach.',
    relationship: 'Use this when you already have dishes. Move from library recipes into a dinner plan when you are ready to schedule service.',
    icon: '\u2630',
  },
  {
    key: 'authored-workspace',
    to: '/recipes/new',
    title: 'Start a Recipe Draft',
    navLabel: 'Recipe Drafts',
    cta: 'Open recipe workspace',
    purpose: 'Capture a chef-authored dish in kitchen language before you shape the finer prep and service details.',
    relationship: 'Use this when the dish does not exist yet. Save it here first, then decide whether it belongs in the library or a dinner plan.',
    icon: '\u270E',
  },
] as const;

export const pathwayByKey = Object.fromEntries(PATHWAYS.map((pathway) => [pathway.key, pathway])) as Record<PathwayKey, PathwayMeta>;
