import { apiRequest } from './apiClient';

function queryString(values) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value));
  });
  const encoded = query.toString();
  return encoded ? `?${encoded}` : '';
}

export function listDoctors(includeInactive = true) {
  return apiRequest(`/admin/appointments/doctors?include_inactive=${includeInactive}`);
}

export function createDoctor(values) {
  return apiRequest('/admin/appointments/doctors', {
    method: 'POST',
    body: JSON.stringify(values),
  });
}

export function updateDoctor(doctorId, values) {
  return apiRequest(`/admin/appointments/doctors/${doctorId}`, {
    method: 'PATCH',
    body: JSON.stringify(values),
  });
}

export function listAvailability(filters = {}) {
  return apiRequest(`/admin/appointments/availability${queryString(filters)}`);
}

export function createAvailability(values) {
  return apiRequest('/admin/appointments/availability', {
    method: 'POST',
    body: JSON.stringify(values),
  });
}

export function setAvailabilityStatus(slotId, status) {
  return apiRequest(`/admin/appointments/availability/${slotId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

export function listAdminAppointments(filters = {}) {
  return apiRequest(`/admin/appointments${queryString(filters)}`);
}
