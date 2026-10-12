import { useCallback, useEffect, useMemo, useState } from 'react';
import { AppShell } from '../components/AppShell';
import {
  createAvailability,
  createDoctor,
  listAdminAppointments,
  listAvailability,
  listDoctors,
  setAvailabilityStatus,
  updateDoctor,
} from '../lib/appointmentAdminApi';
import { logError } from '../lib/logger';

function dateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Unknown time';
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function ErrorBanner({ error, retry }) {
  if (!error) return null;
  return (
    <div className="schedule-alert" role="alert">
      <div>
        <strong>We could not complete that schedule request.</strong>
        <p>{error.message || 'Please try again.'}</p>
      </div>
      {retry && (
        <button className="button button--quiet" type="button" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function AppointmentAdminPage() {
  const [doctors, setDoctors] = useState([]);
  const [slots, setSlots] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [appointmentStatus, setAppointmentStatus] = useState('');
  const [doctorForm, setDoctorForm] = useState({
    display_name: '',
    specialty: '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  });
  const [slotForm, setSlotForm] = useState({
    doctor_id: '',
    starts_at: '',
    ends_at: '',
    location_type: 'in_person',
    location: '',
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [doctorPage, slotPage, appointmentPage] = await Promise.all([
        listDoctors(true),
        listAvailability(),
        listAdminAppointments(appointmentStatus ? { status: appointmentStatus } : {}),
      ]);
      setDoctors(doctorPage.items);
      setSlots(slotPage.items);
      setAppointments(appointmentPage.items);
      setSlotForm((current) => ({
        ...current,
        doctor_id:
          current.doctor_id || doctorPage.items.find((doctor) => doctor.is_active)?.id || '',
      }));
    } catch (error_) {
      logError('appointment_admin_load_failed', error_);
      setError(error_);
    } finally {
      setLoading(false);
    }
  }, [appointmentStatus]);

  useEffect(() => {
    // Defer the asynchronous state transition one task so this effect only
    // synchronizes the selected filter with the external API.
    const timer = window.setTimeout(load, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const metrics = useMemo(
    () => ({
      scheduled: appointments.filter((item) => item.status === 'scheduled').length,
      available: slots.filter((item) => item.status === 'available').length,
      booked: slots.filter((item) => item.status === 'booked').length,
      activeDoctors: doctors.filter((item) => item.is_active).length,
    }),
    [appointments, doctors, slots],
  );

  async function handleDoctorSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await createDoctor(doctorForm);
      setDoctors((current) => [...current, created].sort((a, b) => a.display_name.localeCompare(b.display_name)));
      setSlotForm((current) => ({ ...current, doctor_id: current.doctor_id || created.id }));
      setDoctorForm((current) => ({ ...current, display_name: '', specialty: '' }));
    } catch (error_) {
      logError('doctor_create_failed', error_);
      setError(error_);
    } finally {
      setBusy(false);
    }
  }

  async function handleDoctorState(doctor) {
    setBusy(true);
    try {
      const changed = await updateDoctor(doctor.id, { is_active: !doctor.is_active });
      setDoctors((current) => current.map((item) => (item.id === changed.id ? changed : item)));
    } catch (error_) {
      logError('doctor_update_failed', error_);
      setError(error_);
    } finally {
      setBusy(false);
    }
  }

  async function handleSlotSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await createAvailability({
        ...slotForm,
        starts_at: new Date(slotForm.starts_at).toISOString(),
        ends_at: new Date(slotForm.ends_at).toISOString(),
        location: slotForm.location.trim() || null,
      });
      setSlots((current) => [...current, created].sort((a, b) => new Date(a.starts_at) - new Date(b.starts_at)));
      setSlotForm((current) => ({ ...current, starts_at: '', ends_at: '', location: '' }));
    } catch (error_) {
      logError('availability_create_failed', error_);
      setError(error_);
    } finally {
      setBusy(false);
    }
  }

  async function handleSlotState(slot) {
    setBusy(true);
    try {
      const status = slot.status === 'blocked' ? 'available' : 'blocked';
      const changed = await setAvailabilityStatus(slot.id, status);
      setSlots((current) => current.map((item) => (item.id === changed.id ? changed : item)));
    } catch (error_) {
      logError('availability_update_failed', error_);
      setError(error_);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell role="admin">
      <main className="schedule-page">
        <section className="schedule-hero">
          <div>
            <p className="eyebrow">Appointment operations</p>
            <h1>Care team schedule</h1>
            <p>Manage providers, publish safe appointment times, and monitor patient bookings.</p>
          </div>
          <button className="button button--quiet" type="button" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh data'}
          </button>
        </section>

        <ErrorBanner error={error} retry={load} />

        <section className="schedule-metrics" aria-label="Schedule overview">
          <article><span>Scheduled</span><strong>{metrics.scheduled}</strong><small>patient appointments</small></article>
          <article><span>Open times</span><strong>{metrics.available}</strong><small>ready to book</small></article>
          <article><span>Reserved</span><strong>{metrics.booked}</strong><small>booked time slots</small></article>
          <article><span>Providers</span><strong>{metrics.activeDoctors}</strong><small>active doctors</small></article>
        </section>

        <div className="schedule-grid">
          <section className="schedule-panel schedule-panel--wide">
            <div className="schedule-panel__heading">
              <div><p className="eyebrow">Live operations</p><h2>Appointments</h2></div>
              <label className="compact-field">Status
                <select value={appointmentStatus} onChange={(event) => setAppointmentStatus(event.target.value)}>
                  <option value="">All</option><option value="scheduled">Scheduled</option>
                  <option value="cancelled">Cancelled</option><option value="completed">Completed</option>
                  <option value="no_show">No show</option>
                </select>
              </label>
            </div>
            <div className="schedule-table-wrap">
              <table className="schedule-table">
                <thead><tr><th>Patient</th><th>Provider</th><th>Date</th><th>Mode</th><th>Status</th></tr></thead>
                <tbody>
                  {appointments.map((item) => <tr key={item.id}>
                    <td><strong>{item.patient_name || 'Patient'}</strong><small>{item.patient_email || item.patient_id}</small></td>
                    <td><strong>{item.doctor.display_name}</strong><small>{item.doctor.specialty}</small></td>
                    <td>{dateTime(item.starts_at)}</td><td>{item.location_type.replaceAll('_', ' ')}</td>
                    <td><span className={`schedule-status schedule-status--${item.status}`}>{item.status.replaceAll('_', ' ')}</span></td>
                  </tr>)}
                </tbody>
              </table>
              {!loading && !appointments.length && <p className="schedule-empty">No appointments match this view.</p>}
            </div>
          </section>

          <section className="schedule-panel">
            <div className="schedule-panel__heading"><div><p className="eyebrow">Care team</p><h2>Providers</h2></div></div>
            <form className="schedule-form" onSubmit={handleDoctorSubmit}>
              <label>Full name<input required value={doctorForm.display_name} onChange={(event) => setDoctorForm({ ...doctorForm, display_name: event.target.value })} placeholder="Dr. Maya Rivera" /></label>
              <label>Specialty<input required value={doctorForm.specialty} onChange={(event) => setDoctorForm({ ...doctorForm, specialty: event.target.value })} placeholder="Cardiology" /></label>
              <label>Timezone<input required value={doctorForm.timezone} onChange={(event) => setDoctorForm({ ...doctorForm, timezone: event.target.value })} /></label>
              <button className="button button--primary" disabled={busy}>Add provider</button>
            </form>
            <div className="provider-list">{doctors.map((doctor) => <article key={doctor.id}>
              <div><strong>{doctor.display_name}</strong><span>{doctor.specialty} · {doctor.timezone}</span></div>
              <button type="button" disabled={busy} onClick={() => handleDoctorState(doctor)}>{doctor.is_active ? 'Deactivate' : 'Activate'}</button>
            </article>)}</div>
          </section>

          <section className="schedule-panel schedule-panel--wide">
            <div className="schedule-panel__heading"><div><p className="eyebrow">Availability</p><h2>Publish appointment times</h2></div></div>
            <form className="slot-form" onSubmit={handleSlotSubmit}>
              <label>Provider<select required value={slotForm.doctor_id} onChange={(event) => setSlotForm({ ...slotForm, doctor_id: event.target.value })}><option value="">Select provider</option>{doctors.filter((doctor) => doctor.is_active).map((doctor) => <option key={doctor.id} value={doctor.id}>{doctor.display_name} · {doctor.specialty}</option>)}</select></label>
              <label>Starts<input required type="datetime-local" value={slotForm.starts_at} onChange={(event) => setSlotForm({ ...slotForm, starts_at: event.target.value })} /></label>
              <label>Ends<input required type="datetime-local" value={slotForm.ends_at} onChange={(event) => setSlotForm({ ...slotForm, ends_at: event.target.value })} /></label>
              <label>Visit type<select value={slotForm.location_type} onChange={(event) => setSlotForm({ ...slotForm, location_type: event.target.value })}><option value="in_person">In person</option><option value="video">Video</option><option value="phone">Phone</option></select></label>
              <label>Location<input value={slotForm.location} onChange={(event) => setSlotForm({ ...slotForm, location: event.target.value })} placeholder="Clinic or meeting room" /></label>
              <button className="button button--primary" disabled={busy || !slotForm.doctor_id}>Publish time</button>
            </form>
            <div className="slot-list">{slots.map((slot) => <article key={slot.id}>
              <div className="slot-date"><strong>{dateTime(slot.starts_at)}</strong><span>to {dateTime(slot.ends_at)}</span></div>
              <div><strong>{slot.doctor.display_name}</strong><span>{slot.doctor.specialty} · {slot.location_type.replaceAll('_', ' ')}</span></div>
              <span className={`schedule-status schedule-status--${slot.status}`}>{slot.status}</span>
              <button type="button" disabled={busy || slot.status === 'booked'} onClick={() => handleSlotState(slot)}>{slot.status === 'blocked' ? 'Reopen' : 'Block'}</button>
            </article>)}</div>
          </section>
        </div>
      </main>
    </AppShell>
  );
}
