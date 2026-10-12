import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { AdminRoute } from './AdminRoute';
import { resolveLandingRole } from '../lib/apiClient';

vi.mock('../lib/apiClient', () => ({ resolveLandingRole: vi.fn() }));
vi.mock('../lib/logger', () => ({ logError: vi.fn() }));

function renderGuard() {
  return render(
    <MemoryRouter initialEntries={['/admin/chat']}>
      <Routes>
        <Route
          path="/admin/chat"
          element={
            <AdminRoute>
              <div>Administrator workspace</div>
            </AdminRoute>
          }
        />
        <Route path="/patient/chat" element={<div>Patient workspace</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AdminRoute', () => {
  it('renders the protected workspace after backend-confirmed admin access', async () => {
    resolveLandingRole.mockResolvedValue('admin');
    renderGuard();
    expect(await screen.findByText('Administrator workspace')).toBeInTheDocument();
  });

  it('redirects a patient away from the administrator workspace', async () => {
    resolveLandingRole.mockResolvedValue('patient');
    renderGuard();
    expect(await screen.findByText('Patient workspace')).toBeInTheDocument();
    expect(screen.queryByText('Administrator workspace')).not.toBeInTheDocument();
  });
});
