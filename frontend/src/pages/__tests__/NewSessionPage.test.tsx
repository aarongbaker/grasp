import { MemoryRouter } from 'react-router-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { NewSessionPage } from '../NewSessionPage';

const navigateMock = vi.fn();
const createSessionMock = vi.fn();
const runPipelineMock = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

vi.mock('../../api/sessions', () => ({
  createSession: (...args: unknown[]) => createSessionMock(...args),
  runPipeline: (...args: unknown[]) => runPipelineMock(...args),
}));

describe('NewSessionPage', () => {
  beforeEach(() => {
    navigateMock.mockReset();
    createSessionMock.mockReset();
    runPipelineMock.mockReset();
    createSessionMock.mockResolvedValue({ session_id: 'session-123' });
    runPipelineMock.mockResolvedValue({ session_id: 'session-123', status: 'generating', message: 'started' });
  });

  function renderPage() {
    return render(
      <MemoryRouter>
        <NewSessionPage />
      </MemoryRouter>,
    );
  }

  it('defaults to meal-idea mode and preserves the legacy submit path', async () => {
    renderPage();

    fireEvent.change(screen.getByLabelText(/what are you cooking\?/i), {
      target: { value: 'A rustic Italian dinner with handmade pasta.' },
    });
    fireEvent.change(screen.getByLabelText(/^guests$/i), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText(/serving time/i), { target: { value: '18:30' } });
    fireEvent.change(screen.getByLabelText(/dietary restrictions/i), { target: { value: 'Vegetarian' } });
    fireEvent.keyDown(screen.getByLabelText(/dietary restrictions/i), { key: 'Enter' });

    fireEvent.click(screen.getByRole('button', { name: /start planning/i }));

    await waitFor(() => {
      expect(createSessionMock).toHaveBeenCalledWith({
        free_text: 'A rustic Italian dinner with handmade pasta.',
        guest_count: 6,
        meal_type: 'dinner',
        occasion: 'dinner_party',
        dietary_restrictions: ['Vegetarian'],
        serving_time: '18:30',
      });
    });
    expect(runPipelineMock).toHaveBeenCalledWith('session-123');
    expect(navigateMock).toHaveBeenCalledWith('/sessions/session-123');
  });

  it('switches into cookbook mode without calling the legacy submit path and exposes cookbook guard copy', async () => {
    renderPage();

    fireEvent.click(screen.getByRole('tab', { name: /cookbook recipes/i }));

    expect(screen.getByRole('heading', { name: /browse cookbook recipes/i })).toBeInTheDocument();
    expect(screen.getByText(/recipe candidates will appear here once the picker fetch flow is connected/i)).toBeInTheDocument();

    const submitButton = screen.getByRole('button', { name: /start cookbook session/i });
    expect(submitButton).toBeDisabled();

    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(createSessionMock).not.toHaveBeenCalled();
    });
    expect(runPipelineMock).not.toHaveBeenCalled();
    expect(screen.getByText(/select at least one cookbook recipe to continue/i)).toBeInTheDocument();
  });
});
