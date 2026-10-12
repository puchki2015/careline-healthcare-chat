import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuditDrawer } from './AuditDrawer';
import { getSessionAudit, listAuditSessions } from '../lib/auditApi';

vi.mock('../lib/logger', () => ({ logError: vi.fn() }));
vi.mock('../lib/auditApi', () => ({
  getSessionAudit: vi.fn(),
  listAuditSessions: vi.fn(),
}));

describe('AuditDrawer', () => {
  beforeEach(() => {
    listAuditSessions.mockResolvedValue({
      items: [
        {
          id: 'session-1',
          title: 'Patient invitation',
          chat_initiator_role: 'admin',
          subject_patient_id: 'patient-1',
        },
      ],
    });
    getSessionAudit.mockResolvedValue({
      session: {
        id: 'session-1',
        status: 'closed',
        chat_initiator_role: 'admin',
        subject_patient_id: 'patient-1',
      },
      messages: [{ id: 'message-1', message_type: 'text', content: 'Stored message' }],
      runs: [{ id: 'run-1', intent: 'create_patient' }],
      tool_events: [{ id: 'tool-1', tool_name: 'patient_invitation.propose' }],
      pending_actions: [{ id: 'action-1', action_type: 'create_patient', status: 'approved' }],
      context_sources: [],
      patient_summaries: [
        { id: 'summary-1', title: 'Earlier chat', summary: 'First summary' },
        { id: 'summary-2', title: 'Another chat', summary: 'Second summary' },
        { id: 'summary-3', title: 'Oldest chat', summary: 'Third summary' },
      ],
    });
  });

  it('shows the linked patient and their last three summaries', async () => {
    render(<AuditDrawer initialSessionId="session-1" onClose={vi.fn()} />);

    expect(await screen.findByText('First summary')).toBeInTheDocument();
    expect(screen.getByText('Second summary')).toBeInTheDocument();
    expect(screen.getByText('Third summary')).toBeInTheDocument();
    expect(screen.getByText('patient-1')).toBeInTheDocument();
    expect(screen.getByText('LangGraph runs')).toBeInTheDocument();
    expect(screen.queryByText(/token_hash/)).not.toBeInTheDocument();
  });
});
