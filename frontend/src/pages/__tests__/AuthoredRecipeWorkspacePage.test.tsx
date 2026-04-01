import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as authoredRecipesApi from '../../api/authoredRecipes';
import * as sessionsApi from '../../api/sessions';
import { ApiError } from '../../api/client';
import { AuthProvider } from '../../context/AuthContext';
import {
  getAuthoredRecipeValidationDetail,
  translateAuthoredRecipeValidationDetail,
} from '../../utils/errors';
import type { AuthoredRecipeDetail } from '../../types/api';
import { AuthoredRecipeWorkspacePage } from '../AuthoredRecipeWorkspacePage';

const testUserId = '00000000-0000-0000-0000-000000000111';
const storageValues = new Map<string, string>();

function setStorage(key: string, value: string) {
  storageValues.set(key, value);
}

function getStorageItem(key: string) {
  return storageValues.get(key) ?? null;
}

function clearStorage() {
  storageValues.clear();
}

function renderPage(initialEntries: string[] = ['/recipes/new']) {
  setStorage('grasp_token', 'token');
  setStorage('grasp_refresh_token', 'refresh');
  setStorage('grasp_user_id', testUserId);
  setStorage(
    'grasp_user_profile',
    JSON.stringify({
      user_id: testUserId,
      name: 'Test Chef',
      email: 'chef@example.com',
    }),
  );

  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider>
        <AuthoredRecipeWorkspacePage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearStorage();
  vi.stubGlobal('localStorage', {
    getItem: vi.fn((key: string) => getStorageItem(key)),
    setItem: vi.fn((key: string, value: string) => setStorage(key, value)),
    removeItem: vi.fn((key: string) => storageValues.delete(key)),
    clear: vi.fn(() => clearStorage()),
  });
  vi.restoreAllMocks();
});

describe('AuthoredRecipeWorkspacePage', () => {
  it('saves a chef-authored draft through the authored recipe seam', async () => {
    const user = userEvent.setup();
    const createSpy = vi.spyOn(authoredRecipesApi, 'createAuthoredRecipe').mockResolvedValue({
      recipe_id: 'recipe-1234-abcd',
      user_id: testUserId,
      title: 'Charred carrots with whipped feta',
      description: 'A warm vegetable course with smoke, acidity, and a cold dairy contrast.',
      cuisine: 'Levantine',
      yield_info: { quantity: 6, unit: 'plates', notes: 'starter course' },
      ingredients: [
        { name: 'Carrots', quantity: '2 lb', preparation: 'scrubbed' },
        { name: 'Feta', quantity: '8 oz', preparation: 'whipped smooth' },
      ],
      steps: [
        {
          title: 'Roast carrots',
          instruction: 'Roast until deeply caramelized.',
          duration_minutes: 35,
          duration_max: 45,
          resource: 'oven',
          required_equipment: ['sheet tray'],
          dependencies: [],
          can_be_done_ahead: true,
          prep_ahead_window: 'Up to 6 hours ahead',
          prep_ahead_notes: 'Refresh with olive oil before plating',
          target_internal_temperature_f: null,
          until_condition: 'Edges blistered, centers tender',
          yield_contribution: 'Main roasted component',
          chef_notes: 'Do not crowd the tray.',
        },
      ],
      equipment_notes: ['Needs one full sheet tray.'],
      storage: { method: 'Refrigerated', duration: '2 days', notes: 'Store yogurt separately.' },
      hold: { method: 'Warm pass', max_duration: '10 minutes', notes: 'Do not cover tightly.' },
      reheat: { method: 'Hot oven', target: 'Hot through', notes: 'Brush with olive oil.' },
      make_ahead_guidance: 'Roast early, then warm hard before pickup.',
      plating_notes: 'Swipe feta first, then stack the carrots.',
      chef_notes: 'Keep the feta cold for contrast.',
      cookbook_id: null,
      cookbook: null,
      created_at: '2026-04-01T12:00:00Z',
      updated_at: '2026-04-01T12:05:00Z',
    });
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    renderPage();

    await user.type(screen.getByLabelText('Dish title'), 'Charred carrots with whipped feta');
    await user.type(
      screen.getByLabelText('How would you describe the dish at the pass?'),
      'A warm vegetable course with smoke, acidity, and a cold dairy contrast.',
    );
    await user.type(screen.getByLabelText('Cuisine or lens'), 'Levantine');
    await user.clear(screen.getByLabelText('Yield'));
    await user.type(screen.getByLabelText('Yield'), '6');
    await user.clear(screen.getByLabelText('Yield unit'));
    await user.type(screen.getByLabelText('Yield unit'), 'plates');
    await user.type(screen.getByLabelText('Yield note'), 'starter course');

    const ingredientNameInputs = screen.getAllByLabelText(/Ingredient \d+/i);
    const quantityInputs = screen.getAllByLabelText('Quantity');
    await user.type(ingredientNameInputs[0], 'Carrots');
    await user.type(quantityInputs[0], '2 lb');
    await user.type(screen.getByLabelText('Prep note'), 'scrubbed');

    await user.click(screen.getByRole('button', { name: 'Add ingredient' }));
    const updatedIngredientNameInputs = screen.getAllByLabelText(/Ingredient \d+/i);
    const updatedQuantityInputs = screen.getAllByLabelText('Quantity');
    const prepNoteInputs = screen.getAllByLabelText('Prep note');
    await user.type(updatedIngredientNameInputs[1], 'Feta');
    await user.type(updatedQuantityInputs[1], '8 oz');
    await user.type(prepNoteInputs[1], 'whipped smooth');

    await user.type(screen.getByLabelText('Step title'), 'Roast carrots');
    await user.selectOptions(screen.getByLabelText('Where does the work happen?'), 'oven');
    await user.type(screen.getByLabelText('What happens in this beat?'), 'Roast until deeply caramelized.');
    const expectedMinutesInput = screen.getByLabelText('Expected minutes');
    fireEvent.change(expectedMinutesInput, { target: { value: '10' } });
    expect(expectedMinutesInput).toHaveValue(10);
    const outerEdgeInput = screen.getByLabelText('Outer edge if service drifts');
    fireEvent.change(outerEdgeInput, { target: { value: '15' } });
    expect(outerEdgeInput).toHaveValue(15);
    const equipmentInput = screen.getByLabelText('Equipment needed');
    await user.clear(equipmentInput);
    await user.type(equipmentInput, 'sheet tray');
    await user.type(screen.getByLabelText('Until condition'), 'Edges blistered, centers tender');
    await user.type(screen.getByLabelText('Yield contribution'), 'Main roasted component');
    await user.type(screen.getByLabelText('Chef note for this beat'), 'Do not crowd the tray.');
    await user.click(screen.getByLabelText('This beat can be handled ahead of service.'));
    await screen.findByLabelText('How far ahead?');
    await user.type(screen.getByLabelText('How far ahead?'), 'Up to 6 hours ahead');
    await user.type(screen.getByLabelText('Recovery note'), 'Refresh with olive oil before plating');
    await user.type(screen.getByLabelText('Make-ahead guidance'), 'Roast early, then warm hard before pickup.');

    await user.click(screen.getByLabelText('Open hold, storage, and recovery details.'));
    await user.type(screen.getByLabelText('Storage note'), 'Store yogurt separately.');
    const methodInputs = screen.getAllByLabelText('Method', { selector: 'input' });
    await user.type(methodInputs[0], 'Refrigerated');
    await user.type(screen.getByLabelText('How long'), '2 days');

    const holdMethod = methodInputs[1];
    await user.type(holdMethod, 'Warm pass');
    await user.type(screen.getByLabelText('Longest safe hold'), '10 minutes');
    await user.type(screen.getByLabelText('Hold note'), 'Do not cover tightly.');

    const reheatMethod = methodInputs[2];
    await user.type(reheatMethod, 'Hot oven');
    await user.type(screen.getByLabelText('Target'), 'Hot through');
    await user.type(screen.getByLabelText('Plating note'), 'Swipe feta first, then stack the carrots.');
    await user.type(screen.getByLabelText('Whole-dish chef note'), 'Keep the feta cold for contrast.');
    await user.type(screen.getByLabelText('Equipment note'), 'Needs one full sheet tray.');

    const saveButton = screen.getByRole('button', { name: 'Save private recipe draft' });
    expect(saveButton).toBeEnabled();
    const workspaceForm = saveButton.closest('form');
    expect(workspaceForm).not.toBeNull();
    workspaceForm?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    await waitFor(() => expect(createSpy).toHaveBeenCalledTimes(1));
    expect(createSessionSpy).not.toHaveBeenCalled();
    expect(createSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        user_id: testUserId,
        title: 'Charred carrots with whipped feta',
        cuisine: 'Levantine',
        yield_info: expect.objectContaining({ quantity: 6, unit: 'plates', notes: 'starter course' }),
        make_ahead_guidance: 'Roast early, then warm hard before pickup.',
        plating_notes: 'Swipe feta first, then stack the carrots.',
      }),
    );
    expect(createSpy.mock.calls[0][0].ingredients).toEqual([
      { name: 'Carrots', quantity: '2 lb', preparation: 'scrubbed' },
      { name: 'Feta', quantity: '8 oz', preparation: 'whipped smooth' },
    ]);
    expect(createSpy.mock.calls[0][0].steps[0]).toMatchObject({
      title: 'Roast carrots',
      resource: 'oven',
      can_be_done_ahead: true,
      prep_ahead_window: 'Up to 6 hours ahead',
      prep_ahead_notes: 'Refresh with olive oil before plating',
      until_condition: 'Edges blistered, centers tender',
      yield_contribution: 'Main roasted component',
      chef_notes: 'Do not crowd the tray.',
    });
    expect(screen.getByText('Saved as a private recipe draft.')).toBeInTheDocument();
  });

  it('reopens a saved draft through the authored recipe loader and keeps dinner planning separate', async () => {
    const user = userEvent.setup();
    const recipeDetail: AuthoredRecipeDetail = {
      recipe_id: 'recipe-9999',
      user_id: testUserId,
      title: 'Braised greens toast',
      description: 'A late-night snack plate with rich braised greens.',
      cuisine: 'Southern',
      yield_info: { quantity: 4, unit: 'plates', notes: null },
      ingredients: [{ name: 'Greens', quantity: '3 bunches', preparation: 'washed' }],
      steps: [
        {
          title: 'Braise greens',
          instruction: 'Cook until silky and glossy.',
          duration_minutes: 40,
          duration_max: null,
          resource: 'stovetop',
          required_equipment: ['braiser'],
          dependencies: [],
          can_be_done_ahead: false,
          prep_ahead_window: null,
          prep_ahead_notes: null,
          target_internal_temperature_f: null,
          until_condition: null,
          yield_contribution: null,
          chef_notes: null,
        },
      ],
      equipment_notes: [],
      storage: null,
      hold: null,
      reheat: null,
      make_ahead_guidance: null,
      plating_notes: null,
      chef_notes: null,
      cookbook_id: null,
      cookbook: null,
      created_at: '2026-04-01T12:00:00Z',
      updated_at: '2026-04-01T12:05:00Z',
    };
    const getSpy = vi.spyOn(authoredRecipesApi, 'getAuthoredRecipe').mockResolvedValue(recipeDetail);
    const createSessionSpy = vi.spyOn(sessionsApi, 'createSession');

    renderPage();

    await user.type(screen.getByLabelText('Reopen a saved draft'), 'recipe-9999');
    await user.click(screen.getByRole('button', { name: 'Open saved draft' }));

    await waitFor(() => expect(getSpy).toHaveBeenCalledWith('recipe-9999'));
    expect(createSessionSpy).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue('Braised greens toast')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Southern')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Cook until silky and glossy.')).toBeInTheDocument();
    expect(screen.getByText(/saved draft recipe-9/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Need to plan a full dinner instead?' })).toHaveAttribute(
      'href',
      '/sessions/new',
    );

    cleanup();
    getSpy.mockClear();

    renderPage(['/recipes/new?recipeId=recipe-9999']);

    await waitFor(() => expect(getSpy).toHaveBeenCalledWith('recipe-9999'));
    expect(createSessionSpy).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue(recipeDetail.title)).toBeInTheDocument();
    expect(screen.getByDisplayValue(recipeDetail.cuisine)).toBeInTheDocument();
    expect(screen.getByText(/saved draft recipe-9/i)).toBeInTheDocument();
  });

  it('shows chef-readable preflight guidance before save and highlights the right sections', async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByLabelText('This beat can be handled ahead of service.'));
    const saveButton = screen.getByRole('button', { name: 'Save private recipe draft' });

    await user.clear(screen.getByLabelText('Yield'));
    await user.type(screen.getByLabelText('Yield'), '4');

    const form = saveButton.closest('form');
    form?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    expect(await screen.findByText(/parts of the draft still need kitchen detail/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Give the dish a title the kitchen will recognize immediately/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Step 1 needs a beat name/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Step 1 is marked make-ahead, but it still needs a window/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Add whole-dish make-ahead guidance/i).length).toBeGreaterThan(0);
  });

  it('translates backend contradictory timing guidance into kitchen-language copy', () => {
    expect(
      translateAuthoredRecipeValidationDetail({
        detail: [
          {
            type: 'value_error',
            loc: ['body', 'steps', 0, 'duration_max'],
            msg: 'Value error, duration_max must be greater than or equal to duration_minutes',
          },
        ],
      }),
    ).toEqual({
      summary: 'One part of the draft still needs kitchen detail before it can save.',
      fields: [
        {
          path: 'steps.0.duration_max',
          message:
            'Step 1 has an outer time edge that ends before the expected working time. Make the outer edge the same length or longer.',
        },
      ],
    });
  });

  it('translates backend make-ahead misconfiguration into kitchen-language copy', () => {
    expect(
      translateAuthoredRecipeValidationDetail({
        detail: [
          {
            type: 'value_error',
            loc: ['body', 'steps', 0, 'prep_ahead_window'],
            msg: 'Value error, prep_ahead_window is required when can_be_done_ahead is true',
          },
          {
            type: 'value_error',
            loc: ['body', 'steps', 0, 'prep_ahead_notes'],
            msg: 'Value error, prep_ahead_notes is required when can_be_done_ahead is true',
          },
        ],
      }),
    ).toEqual({
      summary: '2 parts of the draft need another pass before this recipe can save.',
      fields: [
        {
          path: 'steps.0.prep_ahead_window',
          message:
            'Step 1 is marked as work you can do ahead, but the make-ahead window is missing. Say how far before service this beat can be handled.',
        },
        {
          path: 'steps.0.prep_ahead_notes',
          message:
            'Step 1 can be done ahead, but the recovery note is missing. Tell the next cook how to bring it back for service.',
        },
      ],
    });
  });

  it('translates backend dependency guidance into visible beat language instead of raw step ids', async () => {
    const user = userEvent.setup();
    vi.spyOn(authoredRecipesApi, 'createAuthoredRecipe').mockRejectedValue(
      new ApiError(
        422,
        'The recipe draft needs more detail before it can be saved.',
        'authored-validation',
        {
          detail: [
            {
              type: 'value_error',
              loc: ['body', 'steps', 1, 'dependencies', 0, 'step_id'],
              msg: "Value error, Step 'braise_greens_step_2' depends on 'missing_step' which does not exist.",
              input: 'missing_step',
            },
          ],
        },
      ),
    );

    renderPage();

    await user.type(screen.getByLabelText('Dish title'), 'Braised greens toast');
    await user.type(screen.getByLabelText('How would you describe the dish at the pass?'), 'Late-night greens on toast.');
    await user.type(screen.getByLabelText('Cuisine or lens'), 'Southern');
    await user.clear(screen.getByLabelText('Yield'));
    await user.type(screen.getByLabelText('Yield'), '4');
    await user.clear(screen.getByLabelText('Yield unit'));
    await user.type(screen.getByLabelText('Yield unit'), 'plates');

    await user.type(screen.getByLabelText('Ingredient 1'), 'Greens');
    await user.type(screen.getByLabelText('Quantity'), '3 bunches');
    await user.type(screen.getByLabelText('Step title'), 'Braise greens');
    await user.type(screen.getByLabelText('What happens in this beat?'), 'Cook until silky and glossy.');

    await user.click(screen.getByRole('button', { name: 'Add step' }));
    await user.type(screen.getByLabelText('Step title'), 'Toast bread');
    await user.type(screen.getByLabelText('What happens in this beat?'), 'Toast just before pickup.');
    await user.selectOptions(screen.getByLabelText('Add a dependency'), '0');

    const workspaceForm = screen.getByRole('button', { name: 'Save private recipe draft' }).closest('form');
    workspaceForm?.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    expect(await screen.findByText(/One part of the draft still needs kitchen detail/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Step 2 points dependency 1 at a beat this draft cannot see yet/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('1. Braise greens').length).toBeGreaterThan(0);
    expect(screen.queryByText(/braise_greens_step_2/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/missing_step/i)).not.toBeInTheDocument();
  });

  it('avoids cookbook and raw-schema wording while exposing chef-readable controls', () => {
    renderPage();

    expect(screen.getByText('Build the draft in passes, not all at once.')).toBeInTheDocument();
    expect(screen.getByText('Open hold, storage, and recovery details.')).toBeInTheDocument();
    expect(screen.getByText('What must finish before this beat starts?')).toBeInTheDocument();
    expect(screen.queryByText(/cookbook/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/schema/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/json/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/depends_on/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/duration_max/i)).not.toBeInTheDocument();
  });

  it('preserves authored validation detail for downstream translation helpers', () => {
    const validationError = new ApiError(
      422,
      'The recipe draft needs more detail before it can be saved.',
      'authored-validation',
      {
        detail: [
          {
            type: 'value_error',
            loc: ['body', 'steps', 0, 'dependencies', 0, 'step_id'],
            msg: "Value error, Step 'charred_carrots_with_whipped_feta_step_1' depends on 'missing_step' which does not exist.",
            input: 'missing_step',
          },
        ],
      },
    );

    expect(getAuthoredRecipeValidationDetail(validationError)).toEqual({
      detail: [
        {
          type: 'value_error',
          loc: ['body', 'steps', 0, 'dependencies', 0, 'step_id'],
          msg: "Value error, Step 'charred_carrots_with_whipped_feta_step_1' depends on 'missing_step' which does not exist.",
          input: 'missing_step',
        },
      ],
    });
    expect(getAuthoredRecipeValidationDetail(new ApiError(500, 'boom'))).toBeNull();
  });

  it('translates structured validation issues into kitchen-language guidance', () => {
    expect(
      translateAuthoredRecipeValidationDetail({
        detail: [
          {
            type: 'value_error',
            loc: ['body', 'steps', 0, 'duration_max'],
            msg: 'Value error, duration_max must be greater than or equal to duration_minutes',
          },
          {
            type: 'value_error',
            loc: ['body', 'steps', 1, 'dependencies', 0, 'step_id'],
            msg: "Value error, Step 'second_step' depends on 'ghost_step' which does not exist.",
          },
        ],
      }),
    ).toEqual({
      summary: '2 parts of the draft need another pass before this recipe can save.',
      fields: [
        {
          path: 'steps.0.duration_max',
          message:
            'Step 1 has an outer time edge that ends before the expected working time. Make the outer edge the same length or longer.',
        },
        {
          path: 'steps.1.dependencies.0.step_id',
          message:
            'Step 2 points dependency 1 at a beat this draft cannot see yet. Re-link it to an earlier visible beat.',
        },
      ],
    });
  });
});
