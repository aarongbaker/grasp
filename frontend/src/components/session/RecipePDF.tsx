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
import { getSessionConceptDisplay, getValidatedRecipeProvenanceDisplay } from './sessionConceptDisplay';

Font.register({
  family: 'Cormorant Garamond',
  fonts: [
    { src: 'https://fonts.gstatic.com/s/cormorantgaramond/v19/co3bmX5slCNuHLi8bLeY9MK7whWMhyjYqXtKhdip.ttf', fontWeight: 400 },
    { src: 'https://fonts.gstatic.com/s/cormorantgaramond/v19/co3ZmX5slCNuHLi8bLeY9MK7whWMhyjQAllvuQWJ5heb_w.ttf', fontWeight: 600 },
  ],
});

Font.register({
  family: 'DM Sans',
  fonts: [
    { src: 'https://fonts.gstatic.com/s/dmsans/v15/rP2Hp2ywxg089UriCZOIHTWEBlw.ttf', fontWeight: 400 },
    { src: 'https://fonts.gstatic.com/s/dmsans/v15/rP2Cp2ywxg089UriASitQKCWBl8.ttf', fontWeight: 500 },
    { src: 'https://fonts.gstatic.com/s/dmsans/v15/rP2Cp2ywxg089UriAWCrQKCWBl8.ttf', fontWeight: 700 },
  ],
});

Font.register({
  family: 'JetBrains Mono',
  fonts: [
    { src: 'https://fonts.gstatic.com/s/jetbrainsmono/v23/tDbY2o-flEEny0FZhsfKuL7DqzI.ttf', fontWeight: 400 },
    { src: 'https://fonts.gstatic.com/s/jetbrainsmono/v23/tDbf2o-flEEny0FZhsfKuL7DqzKp2LQ.ttf', fontWeight: 500 },
  ],
});

const C = {
  bgBase: '#1a1612',
  bgSurface: '#231f1a',
  bgRaised: '#2e2822',
  border: '#3d3530',
  textPrimary: '#f0e8dc',
  textSecondary: '#9e8f80',
  textMuted: '#5c5248',
  accentPrimary: '#c9813a',
  accentWarm: '#d4956a',
  accentCool: '#6a8fa3',
  warning: '#d4a24e',
};

const s = StyleSheet.create({
  page: {
    backgroundColor: C.bgBase,
    paddingTop: 36,
    paddingHorizontal: 34,
    paddingBottom: 40,
    fontFamily: 'DM Sans',
    fontSize: 10,
    color: C.textPrimary,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-end',
    marginBottom: 8,
  },
  title: {
    fontFamily: 'Cormorant Garamond',
    fontSize: 26,
    fontWeight: 600,
    color: C.textPrimary,
    letterSpacing: 0.4,
  },
  headerMeta: {
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    color: C.textMuted,
  },
  divider: {
    borderBottomWidth: 1,
    borderBottomColor: C.border,
    marginBottom: 16,
    marginTop: 6,
  },
  conceptText: {
    fontFamily: 'Cormorant Garamond',
    fontSize: 17,
    color: C.textPrimary,
    marginBottom: 8,
    lineHeight: 1.35,
  },
  conceptMetaRow: {
    flexDirection: 'row',
    gap: 8,
    marginBottom: 8,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  conceptLabel: {
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.accentWarm,
    backgroundColor: C.bgRaised,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#57483f',
    paddingHorizontal: 6,
    paddingVertical: 3,
    textTransform: 'uppercase',
    letterSpacing: 0.9,
  },
  conceptPathway: {
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    color: C.textMuted,
  },
  conceptSourceDetail: {
    fontSize: 9,
    color: C.textSecondary,
    marginBottom: 16,
    lineHeight: 1.55,
  },
  summaryBlock: {
    backgroundColor: C.bgSurface,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: C.border,
    padding: 12,
    marginBottom: 20,
  },
  summaryText: {
    fontSize: 10,
    color: C.textPrimary,
    lineHeight: 1.6,
  },
  sectionLabel: {
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    fontWeight: 500,
    color: C.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 1,
    marginBottom: 8,
    marginTop: 16,
  },
  timelineRow: {
    flexDirection: 'row',
    borderBottomWidth: 0.5,
    borderBottomColor: C.border,
    paddingVertical: 5,
    alignItems: 'flex-start',
  },
  timelineTime: {
    fontFamily: 'JetBrains Mono',
    fontSize: 9,
    color: C.accentWarm,
    width: 50,
  },
  timelineRecipe: {
    fontSize: 8,
    fontWeight: 700,
    color: C.textSecondary,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    width: 104,
  },
  timelineAction: {
    fontSize: 9,
    color: C.textPrimary,
    flex: 1,
    lineHeight: 1.45,
  },
  timelineResource: {
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.textMuted,
    width: 60,
    textAlign: 'right',
  },
  timelineDuration: {
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    color: C.textMuted,
    width: 45,
    textAlign: 'right',
  },
  recipeName: {
    fontFamily: 'Cormorant Garamond',
    fontSize: 20,
    fontWeight: 600,
    color: C.textPrimary,
    marginBottom: 4,
  },
  provenanceRow: {
    flexDirection: 'column',
    gap: 4,
    marginBottom: 8,
  },
  provenanceLabel: {
    alignSelf: 'flex-start',
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.accentWarm,
    backgroundColor: C.bgRaised,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#57483f',
    paddingHorizontal: 6,
    paddingVertical: 3,
    textTransform: 'uppercase',
    letterSpacing: 0.9,
  },
  provenanceDetail: {
    fontSize: 8,
    color: C.textMuted,
    lineHeight: 1.45,
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
    flexWrap: 'wrap',
  },
  recipeMetaItem: {
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    color: C.textMuted,
  },
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
    fontFamily: 'JetBrains Mono',
    fontSize: 8,
    color: C.accentWarm,
    width: 70,
  },
  ingredientName: {
    fontSize: 9,
    color: C.textPrimary,
    flex: 1,
  },
  stepRow: {
    flexDirection: 'row',
    paddingVertical: 4,
    borderBottomWidth: 0.5,
    borderBottomColor: C.border,
    alignItems: 'flex-start',
  },
  stepNum: {
    fontFamily: 'JetBrains Mono',
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
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.textMuted,
    width: 80,
    textAlign: 'right',
  },
  chefNotes: {
    backgroundColor: C.bgSurface,
    borderLeftWidth: 2,
    borderLeftColor: C.accentPrimary,
    borderRadius: 4,
    padding: 10,
    marginTop: 8,
  },
  chefNotesText: {
    fontSize: 9,
    color: C.textSecondary,
    lineHeight: 1.6,
  },
  techniqueRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 6,
    marginTop: 6,
  },
  techniquePill: {
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.accentWarm,
    backgroundColor: C.bgRaised,
    borderRadius: 999,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  sharedPrepBadge: {
    fontFamily: 'JetBrains Mono',
    fontSize: 6,
    color: C.accentCool,
    backgroundColor: '#1f2626',
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#3a4545',
    paddingHorizontal: 6,
    paddingVertical: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginLeft: 4,
  },
  sharedPrepRecipeName: {
    fontSize: 8,
    fontWeight: 700,
    color: C.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    width: 104,
  },
  ovenTempBadge: {
    fontFamily: 'JetBrains Mono',
    fontSize: 6,
    color: C.warning,
    backgroundColor: '#2e2416',
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#4a3820',
    paddingHorizontal: 6,
    paddingVertical: 2,
    letterSpacing: 0.3,
    marginLeft: 4,
  },
  preheatBadge: {
    fontFamily: 'JetBrains Mono',
    fontSize: 6,
    color: C.warning,
    backgroundColor: '#2e2416',
    borderRadius: 999,
    borderWidth: 1,
    borderColor: '#4a3820',
    paddingHorizontal: 6,
    paddingVertical: 2,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    marginLeft: 4,
  },
  preheatRecipeName: {
    fontSize: 8,
    fontWeight: 700,
    color: C.textMuted,
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    width: 104,
  },
  warningText: {
    fontSize: 8,
    color: C.warning,
    paddingVertical: 2,
  },
  footer: {
    position: 'absolute',
    bottom: 22,
    left: 34,
    right: 34,
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  footerText: {
    fontFamily: 'JetBrains Mono',
    fontSize: 7,
    color: C.textMuted,
  },
});

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

function TimelineSection({ label, entries }: { label: string; entries: TimelineEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <View>
      <Text style={s.sectionLabel}>{label}</Text>
      {entries.map((e) => {
        const isMerged = e.merged_from && e.merged_from.length > 0;
        const isPreheat = e.is_preheat === true;
        const hasOvenTemp = e.resource === 'oven' && e.oven_temp_f != null;
        return (
          <View key={e.step_id} style={s.timelineRow} wrap={false}>
            <Text style={s.timelineTime}>{e.label}</Text>
            {isMerged ? (
              <View style={{ width: 104, flexDirection: 'row', alignItems: 'center' }}>
                <Text style={s.sharedPrepRecipeName}>Shared Prep</Text>
                <Text style={s.sharedPrepBadge}>SHARED</Text>
              </View>
            ) : isPreheat ? (
              <View style={{ width: 104, flexDirection: 'row', alignItems: 'center' }}>
                <Text style={s.preheatRecipeName}>Preheat</Text>
                <Text style={s.preheatBadge}>PREHEAT</Text>
              </View>
            ) : (
              <Text style={s.timelineRecipe}>{e.recipe_name}</Text>
            )}
            <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center' }}>
              <Text style={s.timelineAction}>{e.action}</Text>
              {hasOvenTemp && <Text style={s.ovenTempBadge}>{e.oven_temp_f}°F</Text>}
            </View>
            <Text style={s.timelineResource}>{RESOURCE_LABELS[e.resource]}</Text>
            <Text style={s.timelineDuration}>{fmtDuration(e.duration_minutes, e.duration_max)}</Text>
          </View>
        );
      })}
    </View>
  );
}

function RecipeSection({ recipe }: { recipe: ValidatedRecipe }) {
  const raw = recipe.source.source;
  const enriched = recipe.source;
  const provenance = getValidatedRecipeProvenanceDisplay(recipe);

  return (
    <View break>
      <View style={s.provenanceRow}>
        <Text style={s.provenanceLabel}>{provenance.label}</Text>
        <Text style={s.provenanceDetail}>{provenance.detail}</Text>
      </View>
      <Text style={s.recipeName}>{raw.name}</Text>
      <Text style={s.recipeDescription}>{raw.description}</Text>

      <View style={s.recipeMeta}>
        <Text style={s.recipeMetaItem}>{raw.cuisine}</Text>
        <Text style={s.recipeMetaItem}>{raw.servings} servings</Text>
        <Text style={s.recipeMetaItem}>~{raw.estimated_total_minutes}m</Text>
        <Text style={s.recipeMetaItem}>{raw.ingredients.length} ingredients</Text>
      </View>

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

      {enriched.chef_notes ? (
        <View style={s.chefNotes} wrap={false}>
          <Text style={s.sectionLabel}>Chef Notes</Text>
          <Text style={s.chefNotesText}>{enriched.chef_notes}</Text>
        </View>
      ) : null}

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

export interface RecipePDFProps {
  session: Session;
  results: SessionResults;
}

export function RecipePDF({ session, results }: RecipePDFProps) {
  const schedule = results.schedule;
  const conceptDisplay = getSessionConceptDisplay(session.concept_json);
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
      <Page size="A4" style={s.page}>
        <View style={s.headerRow}>
          <Text style={s.title}>grasp</Text>
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
          <Text style={s.footerText}>Generated by grasp</Text>
          <Text style={s.footerText} render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>

      <Page size="A4" style={s.page} wrap>
        <Text style={s.sectionLabel}>Recipes</Text>
        <View style={s.divider} />
        {results.recipes.map((recipe) => (
          <RecipeSection key={recipe.source.source.name} recipe={recipe} />
        ))}

        <View style={s.footer} fixed>
          <Text style={s.footerText}>Generated by grasp</Text>
          <Text style={s.footerText} render={({ pageNumber, totalPages }) => `${pageNumber} / ${totalPages}`} />
        </View>
      </Page>
    </Document>
  );
}
