import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as authoredRecipesApi from '../../api/authoredRecipes';
import * as recipeCookbooksApi from '../../api/recipeCookbooks';
import { AuthContext } from '../../context/auth-context';
import { RecipeLibraryPage } from '../RecipeLibraryPage';
import type { UserProfile } from '../../types/api';

const mockLogout = vi.fn();
const mockSetUser = vi.fn();

const authValue = {
  token: 'token',
  userId: 'user-1',
  user: {
    user_id: 'user-1',
    email: 'chef@example.com',
    name: 'Chef Mira',
    kitchen_config_id: null,
    kitchen_config: null,
    dietary_defaults: [],
    equipment: [],
    created_at: '2026-04-01T00:00:00Z',
  } satisfies UserProfile,
  isAuthenticated: true,
  login: vi.fn(),
  logout: mockLogout,
  setUser: mockSetUser,
};

function renderWithAuth() {
  return render(
    <AuthContext.Provider value={authValue}>
      <MemoryRouter>
        <RecipeLibraryPage />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe('RecipeLibraryPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockLogout.mockReset();
    mockSetUser.mockReset();
  });

  afterEach(() => {
    cleanup();
  });

  it('shows the empty-state library surface with a drafting entry point', async () => {
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([]);
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);

    renderWithAuth();

    expect(screen.getByLabelText('Loading recipe library')).toBeInTheDocument();

    expect(await screen.findByRole('heading', { name: 'No saved dishes yet.' })).toBeInTheDocument();
    const draftLinks = screen.getAllByRole('link', { name: 'Start a New Draft' });
    expect(draftLinks.some((link) => link.getAttribute('href') === '/recipes/new')).toBe(true);
    expect(screen.queryByText(/No data found/i)).not.toBeInTheDocument();
  });

  it('groups saved recipes into cookbook folders and leaves unassigned drafts visible', async () => {
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([
      {
        cookbook_id: 'cookbook-dessert',
        user_id: 'user-1',
        name: 'Dessert',
        description: 'Sweet finishes and plated fruit.',
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-01T00:00:00Z',
      },
    ]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-1',
        user_id: 'user-1',
        title: 'Olive oil cake',
        cuisine: 'Italian',
        cookbook_id: 'cookbook-dessert',
        cookbook: {
          cookbook_id: 'cookbook-dessert',
          name: 'Dessert',
          description: 'Sweet finishes and plated fruit.',
        },
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-02T00:00:00Z',
      },
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    renderWithAuth();

    expect(await screen.findByRole('heading', { name: 'Unassigned recipes' })).toBeInTheDocument();
    expect(screen.getByText('Marinated peppers')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Dessert' })).toBeInTheDocument();
    expect(screen.getByText('Olive oil cake')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: /reopen in workspace/i })[0]).toHaveAttribute(
      'href',
      '/recipes/new?recipeId=recipe-2',
    );
  });

  it('creates cookbooks and moves recipes into them with inline recovery on failure', async () => {
    const user = userEvent.setup();
    vi.spyOn(recipeCookbooksApi, 'listRecipeCookbooks').mockResolvedValue([]);
    vi.spyOn(authoredRecipesApi, 'listAuthoredRecipes').mockResolvedValue([
      {
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        cuisine: 'Spanish',
        cookbook_id: null,
        cookbook: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
      },
    ]);

    const createCookbookSpy = vi.spyOn(recipeCookbooksApi, 'createRecipeCookbook').mockResolvedValue({
      cookbook_id: 'cookbook-mexican',
      user_id: 'user-1',
      name: 'Mexican',
      description: 'Regional dishes and masa work.',
      created_at: '2026-04-01T00:00:00Z',
      updated_at: '2026-04-01T00:00:00Z',
    });
    const moveSpy = vi
      .spyOn(authoredRecipesApi, 'updateAuthoredRecipeCookbook')
      .mockRejectedValueOnce(new Error('Could not move that recipe just now.'))
      .mockResolvedValueOnce({
        recipe_id: 'recipe-2',
        user_id: 'user-1',
        title: 'Marinated peppers',
        description: 'Bright peppers for the station.',
        cuisine: 'Spanish',
        cookbook_id: 'cookbook-mexican',
        cookbook: {
          cookbook_id: 'cookbook-mexican',
          name: 'Mexican',
          description: 'Regional dishes and masa work.',
        },
        yield_info: { quantity: 4, unit: 'plates', notes: null },
        ingredients: [],
        steps: [],
        equipment_notes: [],
        storage: null,
        hold: null,
        reheat: null,
        make_ahead_guidance: null,
        plating_notes: null,
        chef_notes: null,
        created_at: '2026-04-01T00:00:00Z',
        updated_at: '2026-04-04T00:00:00Z',
      });

    renderWithAuth();

    await screen.findByText('Marinated peppers');

    await user.type(screen.getByLabelText('Cookbook name'), 'Mexican');
    await user.type(screen.getByLabelText('What belongs here?'), 'Regional dishes and masa work.');
    await user.click(screen.getByRole('button', { name: 'Create Cookbook' }));

    await waitFor(() => expect(createCookbookSpy).toHaveBeenCalledWith({
      name: 'Mexican',
      description: 'Regional dishes and masa work.',
    }));
    expect(screen.getByRole('heading', { name: 'Mexican' })).toBeInTheDocument();

    const moveSelect = screen.getByLabelText('Move to cookbook');
    await user.selectOptions(moveSelect, 'cookbook-mexican');
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not move that recipe just now.');

    await user.selectOptions(screen.getByLabelText('Move to cookbook'), 'cookbook-mexican');
    await waitFor(() => expect(moveSpy).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('Every saved draft is currently tucked into a cookbook folder.')).toBeInTheDocument();
    expect(screen.getByText('Marinated peppers')).toBeInTheDocument();
  });
});
