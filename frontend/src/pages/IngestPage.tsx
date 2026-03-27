import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  uploadPdf,
  getIngestionStatus,
  listCookbooks,
  listDetectedCookbookRecipes,
  deleteCookbook,
} from '../api/ingest';
import { FileUpload } from '../components/shared/FileUpload';
import { Button } from '../components/shared/Button';
import { usePolling } from '../hooks/usePolling';
import type { BookRecord, DetectedCookbookRecipe, IngestionJob } from '../types/api';
import { getErrorMessage } from '../utils/errors';
import styles from './IngestPage.module.css';

type RecipeGroups = Array<{
  bookId: string;
  bookTitle: string;
  recipes: DetectedCookbookRecipe[];
}>;

function formatRecipeLocation(recipe: DetectedCookbookRecipe): string {
  const parts: string[] = [];

  if (recipe.chapter) {
    parts.push(recipe.chapter);
  }

  if (recipe.page_number !== null) {
    parts.push(`Page ${recipe.page_number}`);
  }

  return parts.join(' • ') || 'Location unavailable';
}

function formatRecipeText(text: string): string {
  return text.trim() || 'No source text available for this candidate.';
}

export function IngestPage() {
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [cookbooks, setCookbooks] = useState<BookRecord[]>([]);
  const [detectedRecipes, setDetectedRecipes] = useState<DetectedCookbookRecipe[]>([]);
  const [recipesLoading, setRecipesLoading] = useState(true);
  const [recipesError, setRecipesError] = useState('');

  const fetchCookbooks = useCallback(async () => {
    try {
      const books = await listCookbooks();
      setCookbooks(books);
      setError('');
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not load your cookbook library'));
    }
  }, []);

  const fetchDetectedRecipes = useCallback(async () => {
    setRecipesLoading(true);

    try {
      const recipes = await listDetectedCookbookRecipes();
      setDetectedRecipes(recipes);
      setRecipesError('');
    } catch (err: unknown) {
      setRecipesError(getErrorMessage(err, 'Could not load detected recipe candidates'));
    } finally {
      setRecipesLoading(false);
    }
  }, []);

  const refreshCookbookData = useCallback(async () => {
    await Promise.all([fetchCookbooks(), fetchDetectedRecipes()]);
  }, [fetchCookbooks, fetchDetectedRecipes]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void refreshCookbookData();
    }, 0);
    return () => window.clearTimeout(id);
  }, [refreshCookbookData]);

  const { data: job } = usePolling<IngestionJob>({
    fetcher: () => getIngestionStatus(jobId!),
    interval: 3000,
    shouldStop: (j) => {
      if (j.status === 'complete' || j.status === 'failed') {
        if (j.status === 'complete') {
          setFile(null);
          void refreshCookbookData();
        }
        setJobId(null);
        return true;
      }
      return false;
    },
    enabled: !!jobId,
  });

  const recipeGroups = useMemo<RecipeGroups>(() => {
    const groups = new Map<string, { bookId: string; bookTitle: string; recipes: DetectedCookbookRecipe[] }>();

    for (const recipe of detectedRecipes) {
      const existing = groups.get(recipe.book_id);
      if (existing) {
        existing.recipes.push(recipe);
      } else {
        groups.set(recipe.book_id, {
          bookId: recipe.book_id,
          bookTitle: recipe.book_title,
          recipes: [recipe],
        });
      }
    }

    return Array.from(groups.values());
  }, [detectedRecipes]);

  async function handleUpload() {
    if (!file) return;
    setError('');
    setUploading(true);
    try {
      const res = await uploadPdf(file);
      setJobId(res.job_id);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(bookId: string) {
    setError('');
    setRecipesError('');
    try {
      await deleteCookbook(bookId);
      setCookbooks((prev) => prev.filter((book) => book.book_id !== bookId));
      setDetectedRecipes((prev) => prev.filter((recipe) => recipe.book_id !== bookId));
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'Could not delete cookbook'));
    }
  }

  return (
    <div>
      <h1 className={styles.title}>Cookbooks</h1>
      <p className={styles.subtitle}>
        Upload your cookbooks to enrich recipe generation with your personal library.
      </p>

      <div className={styles.uploadSection}>
        <FileUpload onFile={setFile} disabled={uploading} />
        {file && !jobId && (
          <div style={{ marginTop: 'var(--space-md)' }}>
            <Button onClick={handleUpload} disabled={uploading}>
              {uploading ? 'Uploading...' : 'Upload & Process'}
            </Button>
          </div>
        )}
        {error && (
          <p style={{ color: 'var(--cost-negative)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-sm)' }}>
            {error}
          </p>
        )}
      </div>

      {job && (
        <div className={styles.jobStatus}>
          <div className={styles.jobHeader}>
            <span className={styles.jobTitle}>Ingestion Job</span>
            <span className={`${styles.jobBadge} ${styles[job.status]}`}>{job.status}</span>
          </div>
          <div className={styles.bookList}>
            {job.book_statuses.map((b, i) => (
              <div key={i} className={styles.bookItem}>
                <span>{b.title}</span>
                <span>{b.status}</span>
                {b.error && <span className={styles.bookError}>{b.error}</span>}
              </div>
            ))}
          </div>
          {job.status === 'complete' && (
            <p style={{ color: 'var(--cost-positive)', fontSize: 'var(--text-sm)', marginTop: 'var(--space-md)' }}>
              Done! {job.completed} book(s) processed successfully.
            </p>
          )}
        </div>
      )}

      <section className={styles.recipeSection} aria-labelledby="detected-recipes-heading">
        <div className={styles.sectionHeader}>
          <div>
            <h2 id="detected-recipes-heading" className={styles.libraryTitle}>
              Detected Recipe Candidates
            </h2>
            <p className={styles.sectionDescription}>
              Review recipe-like chunks from your uploaded cookbooks to inspect extraction quality and source provenance.
            </p>
          </div>
          {!recipesLoading && !recipesError && detectedRecipes.length > 0 && (
            <span className={styles.sectionBadge}>{detectedRecipes.length} candidate{detectedRecipes.length === 1 ? '' : 's'}</span>
          )}
        </div>

        {recipesLoading ? (
          <p className={styles.infoState}>Loading detected recipe candidates…</p>
        ) : recipesError ? (
          <div className={styles.errorState} role="alert">
            <p className={styles.errorStateTitle}>Could not load detected recipe candidates.</p>
            <p className={styles.errorStateBody}>{recipesError}</p>
          </div>
        ) : detectedRecipes.length === 0 ? (
          <p className={styles.infoState}>
            No detected recipe candidates yet. Upload a cookbook with recipe pages to inspect extracted chunks here.
          </p>
        ) : (
          <div className={styles.recipeGroups}>
            {recipeGroups.map((group) => (
              <section key={group.bookId} className={styles.recipeGroup} aria-labelledby={`cookbook-${group.bookId}`}>
                <div className={styles.recipeGroupHeader}>
                  <div>
                    <h3 id={`cookbook-${group.bookId}`} className={styles.recipeGroupTitle}>
                      {group.bookTitle}
                    </h3>
                    <p className={styles.recipeGroupMeta}>
                      {group.recipes.length} candidate{group.recipes.length === 1 ? '' : 's'} from this cookbook
                    </p>
                  </div>
                </div>
                <div className={styles.recipeList}>
                  {group.recipes.map((recipe) => (
                    <article key={recipe.chunk_id} className={styles.recipeCard}>
                      <div className={styles.recipeCardHeader}>
                        <div className={styles.recipeCardMeta}>
                          <span className={styles.recipeLocation}>{formatRecipeLocation(recipe)}</span>
                          <span className={styles.recipeType}>{recipe.chunk_type}</span>
                        </div>
                        <span className={styles.recipeTimestamp}>
                          Detected {new Date(recipe.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <p className={styles.recipeText}>{formatRecipeText(recipe.text)}</p>
                      <dl className={styles.recipeProvenance}>
                        <div>
                          <dt>Book ID</dt>
                          <dd>{recipe.book_id}</dd>
                        </div>
                        <div>
                          <dt>Chunk ID</dt>
                          <dd>{recipe.chunk_id}</dd>
                        </div>
                        <div>
                          <dt>Chapter</dt>
                          <dd>{recipe.chapter ?? 'Unknown'}</dd>
                        </div>
                        <div>
                          <dt>Page</dt>
                          <dd>{recipe.page_number ?? 'Unknown'}</dd>
                        </div>
                      </dl>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>

      <div className={styles.library}>
        <h2 className={styles.libraryTitle}>Your Library</h2>
        {cookbooks.length === 0 ? (
          <p className={styles.emptyLibrary}>
            Your library is empty — upload a cookbook to get started.
          </p>
        ) : (
          <div className={styles.libraryList}>
            {cookbooks.map((book) => (
              <div key={book.book_id} className={styles.libraryItem}>
                <div className={styles.libraryItemHeader}>
                  <span className={styles.libraryItemTitle}>{book.title}</span>
                  <div className={styles.libraryItemActions}>
                    {book.document_type && (
                      <span className={styles.libraryItemType}>{book.document_type}</span>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(book.book_id)}
                      aria-label={`Delete ${book.title}`}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
                <div className={styles.libraryItemMeta}>
                  {book.author && <span>{book.author}</span>}
                  <span>{book.total_pages} pages</span>
                  <span>{book.total_chunks} chunks</span>
                  <span>{new Date(book.created_at).toLocaleDateString()}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
