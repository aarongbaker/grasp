import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ProfilePage } from '../ProfilePage';

const authState = {
  token: 'token',
  userId: 'user-1',
  isAuthenticated: true,
  login: vi.fn(),
  logout: vi.fn(),
  setUser: vi.fn(),
  user: {
    user_id: 'user-1',
    name: 'Test Chef',
    email: 'chef@test.com',
    kitchen_config_id: 'kitchen-1',
    dietary_defaults: ['vegetarian'],
    created_at: '2026-04-01T00:00:00Z',
    kitchen_config: {
      kitchen_config_id: 'kitchen-1',
      max_burners: 4,
      max_oven_racks: 2,
      has_second_oven: false,
      max_second_oven_racks: 2,
    },
    equipment: [],
    library_access: {
      state: 'locked',
      reason: 'Your current subscription no longer includes cookbook library access.',
      has_catalog_access: false,
      billing_state_changed: true,
      access_diagnostics: {
        subscription_snapshot_id: 'snapshot-1',
        subscription_status: 'active',
        sync_state: 'synced',
        provider: 'stripe',
      },
    },
  },
};

vi.mock('../../context/useAuth', () => ({
  useAuth: () => authState,
}));

vi.mock('../../api/users', () => ({
  getProfile: vi.fn(),
  updateKitchen: vi.fn(),
  updateDietaryDefaults: vi.fn(),
  addEquipment: vi.fn(),
  deleteEquipment: vi.fn(),
}));

const billingApiMocks = vi.hoisted(() => ({
  createCheckoutSession: vi.fn(),
  createPortalSession: vi.fn(),
}));

vi.mock('../../api/billing', () => ({
  createCheckoutSession: billingApiMocks.createCheckoutSession,
  createPortalSession: billingApiMocks.createPortalSession,
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <ProfilePage />
    </MemoryRouter>,
  );
}

describe('ProfilePage', () => {
  const assignMock = vi.fn();

  beforeEach(() => {
    vi.restoreAllMocks();
    billingApiMocks.createCheckoutSession.mockReset();
    billingApiMocks.createPortalSession.mockReset();
    assignMock.mockReset();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { assign: assignMock },
    });
    authState.user = {
      user_id: 'user-1',
      name: 'Test Chef',
      email: 'chef@test.com',
      kitchen_config_id: 'kitchen-1',
      dietary_defaults: ['vegetarian'],
      created_at: '2026-04-01T00:00:00Z',
      kitchen_config: {
        kitchen_config_id: 'kitchen-1',
        max_burners: 4,
        max_oven_racks: 2,
        has_second_oven: false,
        max_second_oven_racks: 2,
      },
      equipment: [],
      library_access: {
        state: 'locked',
        reason: 'Your current subscription no longer includes cookbook library access.',
        has_catalog_access: false,
        billing_state_changed: true,
        access_diagnostics: {
          subscription_snapshot_id: 'snapshot-1',
          subscription_status: 'active',
          sync_state: 'synced',
          provider: 'stripe',
        },
      },
    };
  });

  afterEach(() => {
    cleanup();
  });

  it('renders backend-provided library access copy without exposing billing payload details', () => {
    renderPage();

    expect(screen.getByRole('heading', { name: 'Cookbook Library Access' })).toBeInTheDocument();
    expect(screen.getByText('locked')).toBeInTheDocument();
    expect(screen.getByText('Your current subscription no longer includes cookbook library access.')).toBeInTheDocument();
    expect(screen.getByText('Billing state changed')).toBeInTheDocument();
    expect(screen.getByText('Unavailable')).toBeInTheDocument();
    expect(screen.getByText('synced')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
    expect(screen.getByText('snapshot-1')).toBeInTheDocument();
    expect(screen.queryByText(/stripe/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/provider_customer_ref|provider_subscription_ref|plan_code/i)).not.toBeInTheDocument();
  });

  it('renders unavailable access state verbatim when sync fails', () => {
    authState.user = {
      ...authState.user,
      library_access: {
        state: 'unavailable',
        reason: 'Cookbook library access is temporarily unavailable because your subscription state could not be refreshed.',
        has_catalog_access: false,
        billing_state_changed: true,
        access_diagnostics: {
          subscription_snapshot_id: 'snapshot-2',
          subscription_status: 'cancelled',
          sync_state: 'failed',
          provider: 'stripe',
        },
      },
    };

    renderPage();

    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.getByText('Cookbook library access is temporarily unavailable because your subscription state could not be refreshed.')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Activate cookbook access' })).toBeInTheDocument();
  });

  it('starts checkout from locked access using backend-safe billing metadata', async () => {
    const user = userEvent.setup();
    billingApiMocks.createCheckoutSession.mockResolvedValue({
      url: 'https://checkout.stripe.test/session_123',
      subscription_status: 'incomplete',
      sync_state: 'pending',
      subscription_snapshot_id: 'snapshot-9',
    });

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Activate cookbook access' }));

    await waitFor(() => expect(billingApiMocks.createCheckoutSession).toHaveBeenCalledTimes(1));
    expect(assignMock).toHaveBeenCalledWith('https://checkout.stripe.test/session_123');
    expect(screen.getByText('pending')).toBeInTheDocument();
    expect(screen.getByText('incomplete')).toBeInTheDocument();
    expect(screen.getByText('snapshot-9')).toBeInTheDocument();
    expect(screen.queryByText(/provider_customer_ref|provider_subscription_ref|price_/i)).not.toBeInTheDocument();
  });

  it('opens billing management when access is already included', async () => {
    const user = userEvent.setup();
    authState.user = {
      ...authState.user,
      library_access: {
        state: 'included',
        reason: 'Cookbook library access is included with your account.',
        has_catalog_access: true,
        billing_state_changed: false,
        access_diagnostics: {
          subscription_snapshot_id: 'snapshot-3',
          subscription_status: 'active',
          sync_state: 'synced',
          provider: 'stripe',
        },
      },
    };
    billingApiMocks.createPortalSession.mockResolvedValue({
      url: 'https://billing.stripe.test/session_456',
      subscription_status: 'active',
      sync_state: 'synced',
      subscription_snapshot_id: 'snapshot-3',
    });

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Manage billing' }));

    await waitFor(() => expect(billingApiMocks.createPortalSession).toHaveBeenCalledTimes(1));
    expect(assignMock).toHaveBeenCalledWith('https://billing.stripe.test/session_456');
    expect(screen.getByText('Available')).toBeInTheDocument();
  });

  it('surfaces billing action failures without inventing client-side subscription state', async () => {
    const user = userEvent.setup();
    billingApiMocks.createCheckoutSession.mockRejectedValue(new Error('Billing temporarily unavailable'));

    renderPage();

    await user.click(screen.getByRole('button', { name: 'Activate cookbook access' }));

    expect(await screen.findByText('Billing temporarily unavailable')).toBeInTheDocument();
    expect(assignMock).not.toHaveBeenCalled();
    expect(screen.getByText('synced')).toBeInTheDocument();
    expect(screen.getByText('active')).toBeInTheDocument();
  });
});
