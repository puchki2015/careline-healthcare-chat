import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatWorkspace } from './ChatWorkspace';
import * as chatApi from '../lib/chatApi';

vi.mock('../auth/authState', () => ({ useAuth: () => ({ user: { id: 'user-1' } }) }));
vi.mock('./AppShell', () => ({ AppShell: ({ children }) => <div>{children}</div> }));
vi.mock('../lib/logger', () => ({ logError: vi.fn() }));
vi.mock('../lib/chatApi', () => ({
  closeSession: vi.fn(),
  createSession: vi.fn(),
  listMessages: vi.fn(),
  listSessions: vi.fn(),
  renameSession: vi.fn(),
  sendConfirmation: vi.fn(),
  sendMessage: vi.fn(),
  setSessionArchived: vi.fn(),
}));

const session = {
  id: 'session-1',
  title: 'Profile information',
  status: 'active',
  summary: null,
  last_message_at: '2026-10-08T20:00:00Z',
};

function setOnline(value) {
  Object.defineProperty(navigator, 'onLine', { configurable: true, value });
}

describe('ChatWorkspace recovery behavior', () => {
  beforeEach(() => {
    sessionStorage.clear();
    setOnline(true);
    chatApi.listSessions.mockResolvedValue({ items: [session], next_cursor: null });
    chatApi.listMessages.mockResolvedValue({ items: [], next_cursor: null });
    chatApi.renameSession.mockResolvedValue(session);
  });

  it('restores an unsent draft for the same user, role, and session', async () => {
    const first = render(<ChatWorkspace role="patient" />);
    const composer = await screen.findByLabelText('Message Careline');
    fireEvent.change(composer, { target: { value: 'My unsent question' } });
    first.unmount();

    render(<ChatWorkspace role="patient" />);
    expect(await screen.findByDisplayValue('My unsent question')).toBeInTheDocument();
  });

  it('preserves the draft and disables sending while offline', async () => {
    setOnline(false);
    render(<ChatWorkspace role="patient" />);

    expect(await screen.findByText(/You are offline/)).toBeInTheDocument();
    expect(screen.getByLabelText('Message Careline')).toBeDisabled();
    expect(screen.getByLabelText('Send message')).toBeDisabled();
  });

  it('can recreate the latest unresolved historical proposal', async () => {
    chatApi.listMessages.mockResolvedValue({
      next_cursor: null,
      items: [
        {
          id: 'message-1',
          role: 'user',
          message_type: 'text',
          content: 'Update my phone number',
          created_at: '2026-10-08T20:00:00Z',
        },
        {
          id: 'message-2',
          role: 'assistant',
          message_type: 'confirmation_request',
          content: 'Please confirm the proposed change.',
          structured_data: { data: { proposed_change: { key: 'phone', value: '+2131234567' } } },
          created_at: '2026-10-08T20:00:01Z',
        },
      ],
    });
    chatApi.sendMessage.mockResolvedValue({
      action: 'confirmation_required',
      message: 'Please confirm the proposed change.',
      requires_confirmation: true,
      pending_action_id: 'a'.repeat(43),
      data: { proposed_change: { key: 'phone', value: '+2131234567' } },
      turn: { output_message_id: 'message-3' },
    });
    render(<ChatWorkspace role="patient" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Request again' }));

    await waitFor(() =>
      expect(chatApi.sendMessage).toHaveBeenCalledWith(
        'patient',
        'session-1',
        'Update my phone number',
        expect.any(String),
      ),
    );
  });
});
