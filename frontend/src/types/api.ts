/* TypeScript types mirroring backend Pydantic models */

export type MealType = 'breakfast' | 'brunch' | 'lunch' | 'dinner' | 'appetizers' | 'snacks' | 'dessert' | 'meal_prep';
export type Occasion = 'casual' | 'dinner_party' | 'tasting_menu' | 'meal_prep';
export type SessionStatus = 'pending' | 'generating' | 'enriching' | 'validating' | 'scheduling' | 'complete' | 'partial' | 'failed' | 'cancelled';
export type Resource = 'oven' | 'stovetop' | 'passive' | 'hands';
export type IngestionStatus = 'pending' | 'processing' | 'complete' | 'failed';
export type EquipmentCategory = 'precision' | 'baking' | 'prep' | 'specialty';
export type SessionConceptSource = 'free_text' | 'cookbook';

export const TERMINAL_STATUSES: SessionStatus[] = ['complete', 'partial', 'failed', 'cancelled'];
export const IN_PROGRESS_STATUSES: SessionStatus[] = ['generating', 'enriching', 'validating', 'scheduling'];

export const PIPELINE_STAGES: SessionStatus[] = ['generating', 'enriching', 'validating', 'scheduling', 'complete'];

export const MEAL_TYPE_LABELS: Record<MealType, string> = {
  breakfast: 'Breakfast',
  brunch: 'Brunch',
  lunch: 'Lunch',
  dinner: 'Dinner',
  appetizers: 'Appetizers',
  snacks: 'Snacks',
  dessert: 'Dessert',
  meal_prep: 'Meal Prep',
};

export const OCCASION_LABELS: Record<Occasion, string> = {
  casual: 'Casual',
  dinner_party: 'Dinner Party',
  tasting_menu: 'Tasting Menu',
  meal_prep: 'Meal Prep',
};

export const RESOURCE_LABELS: Record<Resource, string> = {
  oven: 'Oven',
  stovetop: 'Stovetop',
  passive: 'Passive',
  hands: 'Hands',
};

// Auth
export interface TokenRequest {
  email: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

// User
export interface CreateUserRequest {
  name: string;
  email: string;
  password: string;
  max_burners?: number;
  max_oven_racks?: number;
  has_second_oven?: boolean;
  dietary_defaults?: string[];
}

export interface KitchenConfig {
  kitchen_config_id: string;
  max_burners: number;
  max_oven_racks: number;
  has_second_oven: boolean;
  max_second_oven_racks: number;
}

export interface Equipment {
  equipment_id: string;
  user_id: string;
  name: string;
  category: EquipmentCategory;
  unlocks_techniques: string[];
}

export interface UserProfile {
  user_id: string;
  name: string;
  email: string;
  kitchen_config_id: string | null;
  dietary_defaults: string[];
  created_at: string;
  kitchen_config: KitchenConfig | null;
  equipment: Equipment[];
}

// Session
export interface SelectedCookbookRecipeRef {
  chunk_id: string;
}

export interface SelectedCookbookRecipe {
  chunk_id: string;
  book_id: string;
  book_title: string;
  text: string;
  chapter: string;
  page_number: number;
}

export interface DinnerConcept {
  free_text: string;
  guest_count: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions: string[];
  serving_time: string | null;
  concept_source?: SessionConceptSource;
  selected_recipes?: SelectedCookbookRecipe[];
}

export interface CreateFreeTextSessionRequest {
  free_text: string;
  guest_count: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreateCookbookSessionRequest {
  guest_count: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
  concept_source: 'cookbook';
  selected_recipes: SelectedCookbookRecipeRef[];
  free_text?: string;
}

export type CreateSessionRequest = CreateFreeTextSessionRequest | CreateCookbookSessionRequest;

export interface Session {
  session_id: string;
  user_id: string;
  status: SessionStatus;
  concept_json: DinnerConcept;
  schedule_summary: string | null;
  total_duration_minutes: number | null;
  error_summary: string | null;
  result_recipes: ValidatedRecipe[] | null;
  result_schedule: NaturalLanguageSchedule | null;
  token_usage: TokenUsage | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface TokenUsage {
  total_input_tokens: number;
  total_output_tokens: number;
  per_node: Array<{ node_name: string; input_tokens: number; output_tokens: number }>;
}

// Pipeline results (from GET /sessions/{id}/results)
export interface Ingredient {
  name: string;
  quantity: string;
  preparation: string;
}

export interface RecipeStep {
  step_id: string;
  description: string;
  duration_minutes: number;
  duration_max: number | null;
  depends_on: string[];
  resource: Resource;
  required_equipment: string[];
  can_be_done_ahead: boolean;
  prep_ahead_window: string | null;
  prep_ahead_notes: string | null;
}

export interface RawRecipe {
  name: string;
  description: string;
  servings: number;
  cuisine: string;
  estimated_total_minutes: number;
  ingredients: Ingredient[];
  steps: string[];
}

export interface EnrichedRecipe {
  source: RawRecipe;
  steps: RecipeStep[];
  rag_sources: string[];
  chef_notes: string;
  techniques_used: string[];
}

export interface ValidatedRecipe {
  source: EnrichedRecipe;
  validated_at: string;
  warnings: string[];
  passed: boolean;
}

export interface TimelineEntry {
  time_offset_minutes: number;
  label: string;
  clock_time: string | null;
  step_id: string;
  recipe_name: string;
  action: string;
  resource: Resource;
  duration_minutes: number;
  duration_max: number | null;
  buffer_minutes: number | null;
  heads_up: string | null;
  is_prep_ahead: boolean;
  prep_ahead_window: string | null;
}

export interface NaturalLanguageSchedule {
  timeline: TimelineEntry[];
  prep_ahead_entries?: TimelineEntry[];
  total_duration_minutes: number;
  total_duration_minutes_max: number | null;
  active_time_minutes: number | null;
  summary: string;
  error_summary: string | null;
}

export interface NodeError {
  node_name: string;
  error_type: string;
  message: string;
  recoverable: boolean;
}

export interface SessionResults {
  schedule: NaturalLanguageSchedule;
  recipes: ValidatedRecipe[];
  errors: NodeError[];
}

// Ingestion
export interface BookStatus {
  title: string;
  status: string;
  phase?: string;
  error?: string;
  book_id?: string;
  pages_done?: number;
  pages_total?: number;
  chunks_total?: number;
  embedded_chunks?: number;
  started_at?: string;
  updated_at?: string;
  completed_at?: string;
}

export interface BookRecord {
  book_id: string;
  title: string;
  author: string;
  document_type: string | null;
  total_pages: number;
  total_chunks: number;
  created_at: string;
}

export interface IngestionJob {
  job_id: string;
  user_id: string;
  status: IngestionStatus;
  book_count: number;
  completed: number;
  failed: number;
  book_statuses: BookStatus[];
  created_at: string;
  completed_at: string | null;
}

export interface DetectedRecipeCandidate {
  chunk_id: string;
  book_id: string;
  book_title: string;
  recipe_name: string;
  chapter: string;
  page_number: number;
  text: string;
}
