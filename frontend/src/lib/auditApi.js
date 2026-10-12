import { apiRequest } from './apiClient';

export function listAuditSessions() {
  return apiRequest('/admin/audit/sessions?limit=100');
}

export function getSessionAudit(sessionId) {
  return apiRequest(`/admin/audit/sessions/${sessionId}`);
}
