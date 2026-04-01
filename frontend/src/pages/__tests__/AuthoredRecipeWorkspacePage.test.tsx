import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AuthoredRecipeWorkspacePage } from '../AuthoredRecipeWorkspacePage';

function renderPage() {
  return render(
    <MemoryRouter>
      <AuthoredRecipeWorkspacePage />
    </MemoryRouter>,
  );
}

describe('AuthoredRecipeWorkspacePage', () => {
  it('renders the chef-first authored workspace shell with progressive sections', () => {
    renderPage();

    expect(
      screen.getByRole('heading', {
        name: 'Open a fresh page for a dish you already know how to talk through.',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Kitchen notebook')).toBeInTheDocument();
    expect(screen.getByText('Blank page, clear structure.')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Build the draft in passes, not all at once.',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Name the dish and the feeling you want on the pass',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Sketch the prep rhythm before you worry about detail',
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Mark what can be made ahead without dulling the dish',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Dish identity')).toBeInTheDocument();
    expect(screen.getByText('Last-minute finishing work')).toBeInTheDocument();
    expect(screen.getByText('Recovery notes if service slips')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue this draft shell' })).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Need to plan a full dinner instead?' }),
    ).toHaveAttribute('href', '/sessions/new');
  });

  it('avoids cookbook-upload and raw-schema wording in the authored shell', () => {
    renderPage();

    expect(screen.queryByText(/cookbook/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/upload/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/schema/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/json/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\bAPI\b/)).not.toBeInTheDocument();
    expect(screen.queryByText(/session lifecycle/i)).not.toBeInTheDocument();
  });
});
