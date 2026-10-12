import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthContext } from '../auth/authState';
import {
  createDoctor,
  listAdminAppointments,
  listAvailability,
  listDoctors,
} from '../lib/appointmentAdminApi';
import { AppointmentAdminPage } from './AppointmentAdminPage';

vi.mock('../lib/logger', () => ({ logError: vi.fn() }));
vi.mock('../lib/appointmentAdminApi', () => ({
  createAvailability: vi.fn(),
  createDoctor: vi.fn(),
  listAdminAppointments: vi.fn(),
  listAvailability: vi.fn(),
  listDoctors: vi.fn(),
  setAvailabilityStatus: vi.fn(),
  updateDoctor: vi.fn(),
}));

const doctor = {
  id: '11111111-1111-4111-8111-111111111111',
  display_name: 'Dr. Maya Rivera',
  specialty: 'Cardiology',
  timezone: 'America/Vancouver',
  is_active: true,
  created_at: '2098-01-01T00:00:00Z',
  updated_at: '2098-01-01T00:00:00Z',
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/admin/appointments']}>
      <AuthContext.Provider
        value={{ user: { email: 'admin@example.com' }, signOut: vi.fn() }}
      >
        <AppointmentAdminPage />
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe('AppointmentAdminPage', () => {
  beforeEach(() => {
    listDoctors.mockResolvedValue({ items: [doctor] });
    listAvailability.mockResolvedValue({ items: [] });
    listAdminAppointments.mockResolvedValue({ items: [] });
    createDoctor.mockResolvedValue({ ...doctor, id: '22222222-2222-4222-8222-222222222222' });
  });

  it('loads the integrated scheduling dashboard', async () => {
    renderPage();
    expect(await screen.findByText('Dr. Maya Rivera')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Care team schedule' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Appointments' })).toHaveClass('active');
    expect(screen.getByText('No appointments match this view.')).toBeInTheDocument();
  });

  it('creates a provider from the frontend form', async () => {
    renderPage();
    await screen.findByText('Dr. Maya Rivera');
    fireEvent.change(screen.getByLabelText('Full name'), {
      target: { value: 'Dr. Alex Chen' },
    });
    fireEvent.change(screen.getByLabelText('Specialty'), {
      target: { value: 'Dermatology' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add provider' }));
    await waitFor(() =>
      expect(createDoctor).toHaveBeenCalledWith({
        display_name: 'Dr. Alex Chen',
        specialty: 'Dermatology',
        timezone: expect.any(String),
      }),
    );
  });
});
