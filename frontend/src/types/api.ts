/* TypeScript types mirroring backend Pydantic models */

export type MealType = 'breakfast' | 'brunch' | 'lunch' | 'dinner' | 'appetizers' | 'snacks' | 'dessert' | 'meal_prep';
export type Occasion = 'casual' | 'dinner_party' | 'tasting_menu' | 'meal_prep';
export type SessionStatus = 'pending' | 'generating' | 'enriching' | 'validating' | 'scheduling' | 'complete' | 'partial' | 'failed' | 'cancelled';
export type Resource = 'oven' | 'stovetop' | 'passive' | 'hands';
export type EquipmentCategory = 'precision' | 'baking' | 'prep' | 'specialty';
export type AuthoredDependencyKind = 'finish_to_start';
export type DinnerConceptSource =
  | 'free_text'
  | 'cookbook'
  | 'authored'
  | 'planner_authored_anchor'
  | 'planner_cookbook_target'
  | 'planner_catalog_cookbook';
export type PlannerCookbookPlanningMode = 'strict' | 'cookbook_biased';
export type PlannerReferenceKind = 'authored' | 'cookbook';
export type PlannerResolutionMatchStatus = 'no_match' | 'resolved' | 'ambiguous';
export type CatalogCookbookAccessState = 'included' | 'preview' | 'locked';
export type CatalogCookbookAudience = 'included' | 'preview' | 'premium';
export type LibraryAccessState = 'included' | 'locked' | 'unavailable';

export interface CatalogAccessDiagnostics {
  subscription_snapshot_id: string | null;
  subscription_status: string | null;
  sync_state: string | null;
  provider: string | null;
}

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

export const PLANNER_COOKBOOK_MODE_LABELS: Record<PlannerCookbookPlanningMode, string> = {
  strict: 'Strict to this cookbook',
  cookbook_biased: 'Cookbook-biased',
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
  invite_code?: string;
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
  library_access: LibraryAccessSummary;
}

export interface LibraryAccessSummary {
  state: LibraryAccessState;
  reason: string;
  has_catalog_access: boolean;
  billing_state_changed: boolean;
  access_diagnostics: {
    subscription_snapshot_id: string | null;
    subscription_status: string | null;
    sync_state: string | null;
    provider: string | null;
  };
}

export interface BillingSessionResponse {
  url: string;
  subscription_status: string | null;
  sync_state: string | null;
  subscription_snapshot_id: string | null;
}

// Session
export interface SelectedCookbookRecipe {
  chunk_id: string;
  book_id: string;
  book_title: string;
  text: string;
  chapter: string;
  page_number: number;
}

export interface SelectedAuthoredRecipe {
  recipe_id: string;
  title: string;
}

export interface PlannerLibraryAuthoredRecipeAnchor {
  recipe_id: string;
  title: string;
}

export interface PlannerLibraryCookbookTarget {
  cookbook_id: string;
  name: string;
  description: string | null;
  mode: PlannerCookbookPlanningMode;
}

export interface PlannerCatalogCookbookReference {
  catalog_cookbook_id: string;
  slug: string;
  title: string;
  access_state: CatalogCookbookAccessState;
  access_state_reason: string;
  access_diagnostics: CatalogAccessDiagnostics | null;
}

export interface PlannerReferenceResolutionRequest {
  kind: PlannerReferenceKind;
  reference: string;
}

export interface PlannerAuthoredResolutionMatch {
  kind: 'authored';
  recipe_id: string;
  title: string;
}

export interface PlannerCookbookResolutionMatch {
  kind: 'cookbook';
  cookbook_id: string;
  name: string;
  description: string | null;
}

export type PlannerResolutionMatch = PlannerAuthoredResolutionMatch | PlannerCookbookResolutionMatch;

export interface PlannerReferenceResolutionResponse {
  kind: PlannerReferenceKind;
  reference: string;
  status: PlannerResolutionMatchStatus;
  matches: PlannerResolutionMatch[];
}

export interface DinnerConcept {
  free_text: string;
  guest_count: number;
  dish_count?: number | null;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions: string[];
  serving_time: string | null;
  concept_source?: DinnerConceptSource;
  selected_recipes?: SelectedCookbookRecipe[];
  selected_authored_recipe?: SelectedAuthoredRecipe | null;
  planner_authored_recipe_anchor?: PlannerLibraryAuthoredRecipeAnchor | null;
  planner_cookbook_target?: PlannerLibraryCookbookTarget | null;
  planner_catalog_cookbook?: PlannerCatalogCookbookReference | null;
}

export interface CreateFreeTextSessionRequest {
  concept_source?: 'free_text';
  free_text: string;
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreateSessionAuthoredSelection {
  recipe_id: string;
  title: string;
}

export interface CreateSessionCookbookSelection {
  chunk_id: string;
}

export interface CreateSessionPlannerAuthoredAnchor {
  recipe_id: string;
  title: string;
}

export interface CreateSessionPlannerCookbookTarget {
  cookbook_id: string;
  name: string;
  mode: PlannerCookbookPlanningMode;
}

export interface CreateSessionPlannerCatalogCookbook {
  catalog_cookbook_id: string;
}

export interface CreateCookbookSessionRequest {
  concept_source: 'cookbook';
  free_text: string;
  selected_recipes: CreateSessionCookbookSelection[];
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreateAuthoredSessionRequest {
  concept_source: 'authored';
  free_text: string;
  selected_authored_recipe: CreateSessionAuthoredSelection;
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreatePlannerAuthoredAnchorSessionRequest {
  concept_source: 'planner_authored_anchor';
  free_text: string;
  planner_authored_recipe_anchor: CreateSessionPlannerAuthoredAnchor;
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreatePlannerCookbookTargetSessionRequest {
  concept_source: 'planner_cookbook_target';
  free_text: string;
  planner_cookbook_target: CreateSessionPlannerCookbookTarget;
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export interface CreatePlannerCatalogCookbookSessionRequest {
  concept_source: 'planner_catalog_cookbook';
  free_text: string;
  planner_catalog_cookbook: CreateSessionPlannerCatalogCookbook;
  guest_count: number;
  dish_count?: number;
  meal_type: MealType;
  occasion: Occasion;
  dietary_restrictions?: string[];
  serving_time?: string;
}

export type CreateSessionRequest =
  | CreateFreeTextSessionRequest
  | CreateCookbookSessionRequest
  | CreateAuthoredSessionRequest
  | CreatePlannerAuthoredAnchorSessionRequest
  | CreatePlannerCookbookTargetSessionRequest
  | CreatePlannerCatalogCookbookSessionRequest;

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

export interface RecipeProvenance {
  kind: 'generated' | 'library_authored' | 'library_cookbook';
  source_label: string | null;
  recipe_id: string | null;
  cookbook_id: string | null;
}

export interface RawRecipe {
  name: string;
  description: string;
  servings: number;
  cuisine: string;
  estimated_total_minutes: number;
  ingredients: Ingredient[];
  steps: string[];
  provenance: RecipeProvenance;
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
  prep_ahead_notes: string | null;
  burner_id?: string | null;
  burner_position?: string | null;
  burner_size?: string | null;
  burner_label?: string | null;
  burner?: {
    burner_id: string;
    position?: string | null;
    size?: string | null;
    label?: string | null;
  } | null;
  // M018 merged prep and oven features:
  merged_from?: string[];
  allocation?: Record<string, string>;
  oven_temp_f?: number | null;
  is_preheat?: boolean;
}

export type OneOvenConflictClassification = 'compatible' | 'resequence_required' | 'irreconcilable';

export interface OneOvenConflictRemediation {
  requires_resequencing?: boolean;
  suggested_actions?: string[];
  delaying_recipe_names?: string[];
  blocking_recipe_names?: string[];
  notes?: string | null;
}

export interface OneOvenConflictSummary {
  classification?: OneOvenConflictClassification;
  tolerance_f?: number;
  has_second_oven?: boolean;
  temperature_gap_f?: number | null;
  blocking_recipe_names?: string[];
  affected_step_ids?: string[];
  remediation?: OneOvenConflictRemediation;
}

export interface NaturalLanguageSchedule {
  timeline: TimelineEntry[];
  prep_ahead_entries?: TimelineEntry[];
  total_duration_minutes: number;
  total_duration_minutes_max: number | null;
  active_time_minutes: number | null;
  summary: string;
  error_summary: string | null;
  one_oven_conflict?: OneOvenConflictSummary;
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

// Authored recipes
export interface AuthoredRecipeYield {
  quantity: number;
  unit: string;
  notes: string | null;
}

export interface AuthoredRecipeDependency {
  step_id: string;
  kind: AuthoredDependencyKind;
  lag_minutes: number;
}

export interface AuthoredRecipeStep {
  title: string;
  instruction: string;
  duration_minutes: number;
  duration_max: number | null;
  resource: Resource;
  required_equipment: string[];
  dependencies: AuthoredRecipeDependency[];
  can_be_done_ahead: boolean;
  prep_ahead_window: string | null;
  prep_ahead_notes: string | null;
  target_internal_temperature_f: number | null;
  until_condition: string | null;
  yield_contribution: string | null;
  chef_notes: string | null;
}

export interface AuthoredRecipeStorageGuidance {
  method: string;
  duration: string;
  notes: string | null;
}

export interface AuthoredRecipeHoldGuidance {
  method: string;
  max_duration: string;
  notes: string | null;
}

export interface AuthoredRecipeReheatGuidance {
  method: string;
  target: string | null;
  notes: string | null;
}

export interface AuthoredRecipeBase {
  title: string;
  description: string;
  cuisine: string;
  yield_info: AuthoredRecipeYield;
  ingredients: Ingredient[];
  steps: AuthoredRecipeStep[];
  equipment_notes: string[];
  storage: AuthoredRecipeStorageGuidance | null;
  hold: AuthoredRecipeHoldGuidance | null;
  reheat: AuthoredRecipeReheatGuidance | null;
  make_ahead_guidance: string | null;
  plating_notes: string | null;
  chef_notes: string | null;
}

export interface RecipeCookbookBase {
  name: string;
  description: string;
}

export interface CatalogCookbookSummary {
  catalog_cookbook_id: string;
  slug: string;
  title: string;
  subtitle: string | null;
  cover_image_url: string | null;
  recipe_count: number;
  audience: CatalogCookbookAudience;
  access_state: CatalogCookbookAccessState;
  access_state_reason: string;
  access_diagnostics: CatalogAccessDiagnostics | null;
}

export interface CatalogCookbookDetail extends CatalogCookbookSummary {
  access_diagnostics: CatalogAccessDiagnostics | null;
  description: string;
  sample_recipe_titles: string[];
  tags: string[];
}

export interface CatalogCookbookListResponse {
  items: CatalogCookbookSummary[];
}

export interface CatalogCookbookDetailResponse {
  item: CatalogCookbookDetail;
}

export interface RecipeCookbookCreateRequest extends RecipeCookbookBase {}

export interface RecipeCookbookDetail extends RecipeCookbookBase {
  cookbook_id: string;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface AuthoredRecipeCookbookSummary {
  cookbook_id: string;
  name: string;
  description: string;
}

export interface AuthoredRecipeCreateRequest extends AuthoredRecipeBase {
  user_id: string;
  cookbook_id?: string | null;
}

export interface AuthoredRecipeDetail extends AuthoredRecipeBase {
  recipe_id: string;
  user_id: string;
  cookbook_id: string | null;
  cookbook: AuthoredRecipeCookbookSummary | null;
  created_at: string;
  updated_at: string;
}

export interface AuthoredRecipeListItem {
  recipe_id: string;
  user_id: string;
  title: string;
  cuisine: string;
  cookbook_id: string | null;
  cookbook: AuthoredRecipeCookbookSummary | null;
  created_at: string;
  updated_at: string;
}

export interface AuthoredRecipeCookbookUpdateRequest {
  cookbook_id: string | null;
}

export interface AuthoredRecipeValidationIssue {
  type: string;
  loc: Array<string | number>;
  msg: string;
  input?: unknown;
}

export interface AuthoredRecipeValidationDetail {
  detail: AuthoredRecipeValidationIssue[];
}
