import { useState } from 'react';
import { RESOURCE_LABELS, type ValidatedRecipe } from '../../types/api';
import styles from './RecipeCard.module.css';

export function RecipeCard({ recipe }: { recipe: ValidatedRecipe }) {
  const [expanded, setExpanded] = useState(false);
  const raw = recipe.source.source;
  const enriched = recipe.source;

  return (
    <div className={styles.card}>
      <div className={styles.cardHeader} onClick={() => setExpanded(!expanded)}>
        <h3 className={styles.recipeName}>{raw.name}</h3>
        <p className={styles.description}>{raw.description}</p>
        <div className={styles.headerMeta}>
          <span className={styles.metaPill}>{raw.cuisine}</span>
          <span className={styles.metaPill}>{raw.servings} servings</span>
          <span className={styles.metaPill}>{raw.ingredients.length} ingredients</span>
          <span className={styles.metaPill}>{enriched.steps.length} steps</span>
          {enriched.rag_sources.length > 0 && (
            <span className={styles.ragTag}>from library</span>
          )}
          <span className={styles.expandHint}>{expanded ? 'collapse' : 'expand'}</span>
        </div>
      </div>

      {expanded && (
        <div className={styles.body}>
          {/* Ingredients */}
          <h4 className={styles.sectionLabel}>Ingredients</h4>
          <div className={styles.ingredientList}>
            {raw.ingredients.map((ing, i) => (
              <div key={i} className={styles.ingredient}>
                <span className={styles.ingredientQty}>{ing.quantity}</span>
                {ing.name}
                {ing.preparation && (
                  <span className={styles.ingredientPrep}>, {ing.preparation}</span>
                )}
              </div>
            ))}
          </div>

          {/* Steps */}
          <h4 className={styles.sectionLabel}>Steps</h4>
          <div className={styles.stepList}>
            {enriched.steps.map((step, i) => (
              <div key={step.step_id} className={styles.step}>
                <span className={styles.stepNum}>{i + 1}</span>
                <span className={styles.stepDesc}>{step.description}</span>
                <div className={styles.stepMeta}>
                  <span className={styles.stepDuration}>
                    {step.duration_minutes}m
                    {step.duration_max && step.duration_max !== step.duration_minutes && `–${step.duration_max}m`}
                  </span>
                  <span className={styles.metaPill}>{RESOURCE_LABELS[step.resource]}</span>
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
            <>
              <h4 className={styles.sectionLabel}>Techniques</h4>
              <div className={styles.techniques}>
                {enriched.techniques_used.map((t) => (
                  <span key={t} className={styles.techniquePill}>{t}</span>
                ))}
              </div>
            </>
          )}

          {/* Warnings */}
          {recipe.warnings.length > 0 && (
            <div className={styles.warnings}>
              <h4 className={styles.sectionLabel}>Validation Notes</h4>
              {recipe.warnings.map((w, i) => (
                <div key={i} className={styles.warning}>{w}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
