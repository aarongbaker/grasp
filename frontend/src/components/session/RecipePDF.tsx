import {
  Document,
  Page,
  View,
  Text,
  StyleSheet,
  Font,
} from '@react-pdf/renderer';
import {
  RESOURCE_LABELS,
  type Session,
  type SessionResults,
  type ValidatedRecipe,
  type TimelineEntry,
} from '../../types/api';
import { getSessionConceptDisplay } from './sessionConceptDisplay';

/* ------------------------------------------------------------------ */
/* Font registration — react-pdf needs explicit .ttf URLs              */
/* ------------------------------------------------------------------ */
Font.register({
  family: 'IBM Plex Sans',
  fonts: [
    { src: 'https://fonts.gstatic.com/s/ibmplexsans/v19/zYXgKVElMYYaJe8bpLHnCwDKhdHeFaxOedc.ttf', fontWeight: 400 },
    { src: 'https://fonts.gstatic.com/s/ibmplexsans/v19/zYX9KVElMYYaJe8bpLHnCwDKjSL9AIFsdP3pBms.ttf', fontWeight: 500 },
    { src: 'https://fonts.gstatic.com/s/ibmplexsans/v19/zYX9KVElMYYaJe8bpLHnCwDKjQ76AIFsdP3pBms.ttf', fontWeight: 600 },
    { src: 'https://fonts.gstatic.com/s/ibmplexsans/v19/zYX9KVElMYYaJe8bpLHnCwDKjWr7AIFsdP3pBms.ttf', fontWeight: 700 },
  ],
});

Font.register({
  family: 'IBM Plex Mono',
  fonts: [
    { src: 'https://fonts.gstatic.com/s/ibmplexmono/v19/-F63fjptAgt5VM-kVkqdyU8n5igg1l9kn-s.ttf', fontWeight: 400 },
    { src: 'https://fonts.gstatic.com/s/ibmplexmono/v19/-F6sfjptAgt5VM-kVkqdyU8n1ioSflV1gMoW.ttf', fontWeight: 500 },
  ],
});

/* ------------------------------------------------------------------ */
/* Color palette (matches tokens.css lab-notebook theme)               */
/* ------------------------------------------------------------------ */
const C = {
  bgBase: '#0d1117',
  bgSurface: '#161b22',
  bgRaised: '#1c2128',
  border: '#30363d',
  textPrimary: '#e6edf3',
  textSecondary: '#8b949e',
  textMuted: '#484f58',
  accent: '#58a6ff',
  positive: '#56d364',
  warning: '#d29922',
  negative: '#f85149',
};

/* ------------------------------------------------------------------ */
/* PDF Styles                                                          */
/* ------------------------------------------------------------------ */
const s = StyleSheet.create({
  page: {
    backgroundColor: C.bgBase,
    padding: 40,
    fontFamily: 'IBM Plex Sans',
    fontSize: 10,
    color: C.textPrimary,
  },

  /* Header */
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginBottom: 8,
  },
  title: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 18,
    fontWeight: 600,
    letterSpacing: 1.5,
    textTransform: 'uppercase',
    color: C.textPrimary,
  },
  headerMeta: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.textMuted,
  },
  divider: {
    borderBottomWidth: 1,
    borderBottomColor: C.border,
    marginBottom: 16,
    marginTop: 8,
  },
  conceptText: {
    fontSize: 10,
    color: C.textSecondary,
    marginBottom: 8,
    lineHeight: 1.5,
  },
  conceptMetaRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 8,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  conceptLabel: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 7,
    color: C.accent,
    backgroundColor: C.bgRaised,
    borderRadius: 999,
    paddingHorizontal: 6,
    paddingVertical: 3,
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  conceptPathway: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.textMuted,
  },
  conceptSourceDetail: {
    fontSize: 9,
    color: C.textMuted,
    marginBottom: 16,
    lineHeight: 1.5,
  },
  summaryBlock: {
    backgroundColor: C.bgSurface,
    borderRadius: 4,
    padding: 12,
    marginBottom: 20,
  },
  summaryText: {
    fontSize: 10,
    color: C.textPrimary,
    lineHeight: 1.6,
  },

  /* Section */
  sectionLabel: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    fontWeight: 500,
    color: C.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 8,
    marginTop: 16,
  },

  /* Timeline */
  timelineRow: {
    flexDirection: 'row',
    borderBottomWidth: 0.5,
    borderBottomColor: C.border,
    paddingVertical: 4,
    alignItems: 'flex-start',
  },
  timelineTime: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 9,
    color: C.accent,
    width: 50,
  },
  timelineRecipe: {
    fontSize: 8,
    fontWeight: 600,
    color: C.textSecondary,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    width: 100,
  },
  timelineAction: {
    fontSize: 9,
    color: C.textPrimary,
    flex: 1,
  },
  timelineResource: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 7,
    color: C.textMuted,
    width: 60,
    textAlign: 'right',
  },
  timelineDuration: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.textMuted,
    width: 45,
    textAlign: 'right',
  },

  /* Recipe */
  recipeName: {
    fontSize: 16,
    fontWeight: 600,
    color: C.textPrimary,
    marginBottom: 4,
  },
  recipeDescription: {
    fontSize: 9,
    color: C.textSecondary,
    marginBottom: 12,
    lineHeight: 1.5,
  },
  recipeMeta: {
    flexDirection: 'row',
    gap: 16,
    marginBottom: 12,
  },
  recipeMetaItem: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.textMuted,
  },

  /* Ingredients */
  ingredientGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
  },
  ingredientItem: {
    width: '50%',
    flexDirection: 'row',
    paddingVertical: 2,
  },
  ingredientQty: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.accent,
    width: 70,
  },
  ingredientName: {
    fontSize: 9,
    color: C.textPrimary,
    flex: 1,
  },

  /* Steps */
  stepRow: {
    flexDirection: 'row',
    paddingVertical: 4,
    borderBottomWidth: 0.5,
    borderBottomColor: C.border,
    alignItems: 'flex-start',
  },
  stepNum: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 8,
    color: C.textMuted,
    width: 20,
  },
  stepDesc: {
    fontSize: 9,
    color: C.textPrimary,
    flex: 1,
    lineHeight: 1.5,
  },
  stepMeta: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 7,
    color: C.textMuted,
    width: 80,
    textAlign: 'right',
  },

  /* Chef notes */
  chefNotes: {
    backgroundColor: C.bgSurface,
    borderLeftWidth: 2,
    borderLeftColor: C.accent,
    borderRadius: 4,
    padding: 10,
    marginTop: 8,
  },
  chefNotesText: {
    fontSize: 9,
    color: C.textSecondary,
    lineHeight: 1.6,
  },

  /* Techniques */
  techniqueRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 6,
  },
  techniquePill: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 7,
    color: C.accent,
    backgroundColor: C.bgRaised,
    borderRadius: 2,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },

  /* Warnings */
  warningText: {
    fontSize: 8,
    color: C.warning,
    paddingVertical: 2,
  },

  /* Footer */
  footer: {
    position: 'absolute',
    bottom: 24,
    left: 40,
    right: 40,
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  footerText: {
    fontFamily: 'IBM Plex Mono',
    fontSize: 7,
    color: C.textMuted,
  },
});

/* ------------------------------------------------------------------ */
/* Helper: format duration                                             */
/* ------------------------------------------------------------------ */
function fmtDuration(min: number, max: number | null): string {
  if (max && max !== min) return `${min}–${max}m`;
  return `${min}m`;
}

function fmtTotalDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/* ------------------------------------------------------------------ */
/* Timeline Section                                                    */
/* ------------------------------------------------------------------ */
function TimelineSection({ label, entries }: { label: string; entries: TimelineEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <View>
      <Text style={s.sectionLabel}>{label}</Text>
      {entries.map((e) => (
        <View key={e.step_id} style={s.timelineRow} wrap={false}>
          <Text style={s.timelineTime}>{e.label}</Text>
          <Text style={s.timelineRecipe}>{e.recipe_name}</Text>
          <Text style={s.timelineAction}>{e.action}</Text>
          <Text style={s.timelineResource}>{RESOURCE_LABELS[e.resource]}</Text>
          <Text style={s.timelineDuration}>{fmtDuration(e.duration_minutes, e.duration_max)}</Text>
        </View>
      ))}
    </View>
  );
}

/* ------------------------------------------------------------------ */
/* Recipe Section                                                      */
/* ------------------------------------------------------------------ */
function RecipeSection({ recipe }: { recipe: ValidatedRecipe }) {
  const raw = recipe.source.source;
  const enriched = recipe.source;

  return (
    <View break>
      <Text style={s.recipeName}>{raw.name}</Text>
      <Text style={s.recipeDescription}>{raw.description}</Text>

      <View style={s.recipeMeta}>
        <Text style={s.recipeMetaItem}>{raw.cuisine}</Text>
        <Text style={s.recipeMetaItem}>{raw.servings} servings</Text>
        <Text style={s.recipeMetaItem}>~{raw.estimated_total_minutes}m</Text>
        <Text style={s.recipeMetaItem}>{raw.ingredients.length} ingredients</Text>
      </View>

      {/* Ingredients */}
      <Text style={s.sectionLabel}>Ingredients</Text>
      <View style={s.ingredientGrid}>
        {raw.ingredients.map((ing, i) => (
          <View key={i} style={s.ingredientItem}>
            <Text style={s.ingredientQty}>{ing.quantity}</Text>
            <Text style={s.ingredientName}>
              {ing.name}{ing.preparation ? `, ${ing.preparation}` : ''}
            </Text>
          </View>
        ))}
      </View>

      {/* Steps */}
      <Text style={s.sectionLabel}>Steps</Text>
      {enriched.steps.map((step, i) => (
        <View key={step.step_id} style={s.stepRow} wrap={false}>
          <Text style={s.stepNum}>{i + 1}</Text>
          <Text style={s.stepDesc}>{step.description}</Text>
          <Text style={s.stepMeta}>
            {fmtDuration(step.duration_minutes, step.duration_max)} · {RESOURCE_LABELS[step.resource]}
          </Text>
        </View>
      ))}

      {/* Chef Notes */}
      {enriched.chef_notes ? (
        <View style={s.chefNotes} wrap={false}>
          <Text style={s.sectionLabel}>Chef Notes</Text>
          <Text style={s.chefNotesText}>{enriched.chef_notes}</Text>
        </View>
      ) : null}

      {/* Techniques */}
      {enriched.techniques_used.length > 0 ? (
        <View wrap={false}>
          <Text style={s.sectionLabel}>Techniques</Text>
          <View style={s.techniqueRow}>
            {enriched.techniques_used.map((t) => (
              <Text key={t} style={s.techniquePill}>{t}</Text>
            ))}
          </View>
        </View>
      ) : null}

      {/* Warnings */}
      {recipe.warnings.length > 0 ? (
        <View wrap={false}>
          <Text style={s.sectionLabel}>Validation Notes</Text>
          {recipe.warnings.map((w, i) => (
            <Text key={i} style={s.warningText}>{w}</Text>
          ))}
        </View>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ */
/* Main PDF Document                                                   */
/* ------------------------------------------------------------------ */
export interface RecipePDFProps {
  session: Session;
  results: SessionResults;
}

export function RecipePDF({ session, results }: RecipePDFProps) {
  const schedule = results.schedule;
  const conceptDisplay = getSessionConceptDisplay(session.concept_json);
  // Combine timeline with any legacy prep_ahead_entries (backwards compat with old session data)
  const allEntries = (() => {
    const legacyPrepAhead = schedule.prep_ahead_entries ?? [];
    if (legacyPrepAhead.length > 0) {
      const merged = [...schedule.timeline, ...legacyPrepAhead];
      return merged.sort((a, b) => a.time_offset_minutes - b.time_offset_minutes);
    }
    return schedule.timeline;
  })();
  const date = session.completed_at
    ? new Date(session.completed_at).toLocaleDateString()
    : new Date(session.created_at).toLocaleDateString();

  return (
    <Document>
      {/* Page 1: Header + Schedule */}
      <Page size="A4" style={s.page}>
        <View style={s.headerRow}>
          <Text style={s.title}>GRASP</Text>
          <Text style={s.headerMeta}>
            {date} · {fmtTotalDuration(schedule.total_duration_minutes)} · {results.recipes.length} recipes
          </Text>
        </View>
        <View style={s.divider} />

        <Text style={s.conceptText}>{conceptDisplay.title}</Text>
        <View style={s.conceptMetaRow}>
          <Text style={s.conceptLabel}>{conceptDisplay.sourceLabel}</Text>
          <Text style={s.conceptPathway}>{conceptDisplay.pathwayLabel}</Text>
        </View>
        <Text style={s.conceptSourceDetail}>{conceptDisplay.sourceDetail}</Text>

        {schedule.summary ? (
          <View style={s.summaryBlock}>
            <Text style={s.summaryText}>{schedule.summary}</Text>
          </View>
        ) : null}

        <TimelineSection label="Timeline" entries={allEntries} />

        <View style={s.footer} fixed>
          <Text style={s.footerText}>Generated by GRASP</Text>
          <Text style={s.footerText} render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>

      {/* Recipe Pages */}
      <Page size="A4" style={s.page} wrap>
        <Text style={s.sectionLabel}>Recipes</Text>
        <View style={s.divider} />
        {results.recipes.map((recipe) => (
          <RecipeSection key={recipe.source.source.name} recipe={recipe} />
        ))}

        <View style={s.footer} fixed>
          <Text style={s.footerText}>Generated by GRASP</Text>
          <Text style={s.footerText} render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>
    </Document>
  );
}
