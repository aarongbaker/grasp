import { useState } from 'react';
import { ChevronDownIcon, ChevronUpIcon, AlertTriangleIcon } from 'lucide-react';
import { RESOURCE_LABELS, type Resource, type ValidatedRecipe } from '../../types/api';
import { getValidatedRecipeProvenanceDisplay } from './sessionConceptDisplay';
import styles from './RecipeCard.module.css';

const RESOURCE_STYLE: Record<Resource, string> = {
  hands: styles.resourceHands,
  stovetop: styles.resourceStovetop,
  oven: styles.resourceOven,
  passive: styles.resourcePassive,
};

export function RecipeCard({ recipe }: { recipe: ValidatedRecipe }) {
  const [expanded, setExpanded] = useState(false);
  const raw = recipe.source.source;
  const enriched = recipe.source;
  const provenance = getValidatedRecipeProvenanceDisplay(recipe);

  return (
    <div className={styles.card}>
      <button
        className={styles.cardHeader}
        onClick={() => setExpanded(!expanded)}
      >
        <div className={styles.headerTop}>
          <div className={styles.headerContent}>
            <div className={styles.provenanceBlock}>
              <span className={styles.provenanceLabel}>{provenance.label}</span>
              <span className={styles.provenanceDetail}>{provenance.detail}</span>
            </div>
            <h3 className={styles.recipeName}>{raw.name}</h3>
            <p className={styles.description}>{raw.description}</p>
            <div className={styles.headerMeta}>
              <span className={styles.metaPill}>{raw.cuisine}</span>
              <span className={styles.metaPill}>{raw.servings} servings</span>
              <span className={styles.metaPill}>{raw.ingredients.length} ingredients</span>
              <span className={styles.metaPill}>{enriched.steps.length} steps</span>
            </div>
          </div>
          {expanded
            ? <ChevronUpIcon size={20} className={styles.chevron} />
            : <ChevronDownIcon size={20} className={styles.chevron} />
          }
        </div>
      </button>

      {expanded && (
        <div className={styles.body}>
          {/* Ingredients */}
          <h4 className={styles.sectionLabel}>Ingredients</h4>
          <div className={styles.ingredientList}>
            {raw.ingredients.map((ing, i) => (
              <div key={i} className={styles.ingredient}>
                <span className={styles.ingredientQty}>{ing.quantity}</span>
                <span>{ing.name}</span>
                {ing.preparation && (
                  <span className={styles.ingredientPrep}>({ing.preparation})</span>
                )}
              </div>
            ))}
          </div>

          {/* Steps */}
          <h4 className={styles.sectionLabel}>Steps</h4>
          <div className={styles.stepList}>
            {enriched.steps.map((step, i) => (
              <div key={step.step_id} className={styles.step}>
                <span className={styles.stepNum}>{i + 1}.</span>
                <div className={styles.stepContent}>
                  <p className={styles.stepDesc}>{step.description}</p>
                </div>
                <div className={styles.stepMeta}>
                  <span className={`${styles.stepResource} ${RESOURCE_STYLE[step.resource]}`}>
                    {RESOURCE_LABELS[step.resource]}
                  </span>
                  <span className={styles.stepDuration}>{step.duration_minutes} min</span>
                </div>
              </div>
            ))}
          </div>

          {/* Chef Notes */}
          {enriched.chef_notes && (
            <>
              <h4 className={styles.sectionLabel}>Chef Notes</h4>
              <div className={styles.chefNotes}>{enriched.chef_notes}</div>
            </>
          )}

          {/* Techniques */}
          {enriched.techniques_used.length > 0 && (
            <div className={styles.techniques} style={{ marginTop: 'var(--space-md)' }}>
              {enriched.techniques_used.map((t) => (
                <span key={t} className={styles.techniquePill}>{t}</span>
              ))}
            </div>
          )}

          {/* Warnings */}
          {recipe.warnings.length > 0 && (
            <div className={styles.warnings}>
              {recipe.warnings.map((w, i) => (
                <div key={i} className={styles.warning}>
                  <AlertTriangleIcon size={12} className={styles.warningIcon} />
                  {w}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
