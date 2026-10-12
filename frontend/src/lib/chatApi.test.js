import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest } from './apiClient';
import {
  listMessages,
  renameSession,
  sendConfirmation,
  sendMessage,
  setSessionArchived,
} from './chatApi';

vi.mock('./apiClient', () => ({ apiRequest: vi.fn() }));

describe('role-aware chat API requests', () => {
  beforeEach(() => {
    vi.stubGlobal('crypto', { randomUUID: vi.fn(() => '11111111-1111-4111-8111-111111111111') });
  });

  it('sends a patient message with a fresh idempotency UUID', async () => {
    apiRequest.mockResolvedValue({ action: 'answer' });

    await sendMessage('patient', 'session-1', 'Show my phone number');

    expect(apiRequest).toHaveBeenCalledWith('/chat', {
      method: 'POST',
      body: JSON.stringify({
        session_id: 'session-1',
        client_message_id: '11111111-1111-4111-8111-111111111111',
        message: 'Show my phone number',
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
      }),
    });
  });

  it('reuses an explicitly supplied idempotency UUID for a safe retry', async () => {
    apiRequest.mockResolvedValue({ action: 'answer' });

    await sendMessage(
      'patient',
      'session-1',
      'Show my phone number',
      '22222222-2222-4222-8222-222222222222',
    );

    const body = JSON.parse(apiRequest.mock.calls[0][1].body);
    expect(body.client_message_id).toBe('22222222-2222-4222-8222-222222222222');
  });

  it('uses the administrator confirmation endpoint and exact typed body', async () => {
    apiRequest.mockResolvedValue({ action: 'patient_invited' });

    await sendConfirmation('admin', 'session-2', 'a'.repeat(43), true);

    expect(apiRequest).toHaveBeenCalledWith('/admin/chat', {
      method: 'POST',
      body: JSON.stringify({
        session_id: 'session-2',
        client_message_id: '11111111-1111-4111-8111-111111111111',
        confirmation: { action_id: 'a'.repeat(43), approved: true },
      }),
    });
  });

  it('forwards an opaque cursor when restoring admin messages', async () => {
    apiRequest.mockResolvedValue({ items: [] });

    await listMessages('admin', 'session-3', 'opaque-cursor');

    expect(apiRequest).toHaveBeenCalledWith(
      '/admin/chat/sessions/session-3/messages?limit=50&cursor=opaque-cursor&newest_first=true',
    );
  });

  it('renames and archives through role-owned session routes', async () => {
    apiRequest.mockResolvedValue({ id: 'session-4' });

    await renameSession('patient', 'session-4', 'Profile information');
    await setSessionArchived('patient', 'session-4', true);

    expect(apiRequest).toHaveBeenNthCalledWith(1, '/chat/sessions/session-4', {
      method: 'PATCH',
      body: JSON.stringify({ title: 'Profile information' }),
    });
    expect(apiRequest).toHaveBeenNthCalledWith(2, '/chat/sessions/session-4/archive', {
      method: 'POST',
    });
  });
});
