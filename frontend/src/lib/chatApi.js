import { apiRequest } from './apiClient';

/**
 * Keep all role-specific URL differences in one module. Components can then
 * render the same workspace for patients and administrators without weakening
 * the backend's role checks.
 */
function routesFor(role) {
  const admin = role === 'admin';
  return {
    sessions: admin ? '/admin/chat/sessions' : '/chat/sessions',
    chat: admin ? '/admin/chat' : '/chat',
  };
}

function withCursor(path, cursor, limit) {
  const query = new URLSearchParams({ limit: String(limit) });
  if (cursor) query.set('cursor', cursor);
  return `${path}?${query}`;
}

export function listSessions(role, cursor = null) {
  return apiRequest(withCursor(routesFor(role).sessions, cursor, 30));
}

export function createSession(role) {
  return apiRequest(routesFor(role).sessions, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function listMessages(role, sessionId, cursor = null) {
  const base = `${routesFor(role).sessions}/${sessionId}/messages`;
  // The live workspace needs the newest page. FastAPI reverses that database
  // page back into chronological order before returning it for display.
  return apiRequest(`${withCursor(base, cursor, 50)}&newest_first=true`);
}

export function closeSession(role, sessionId) {
  return apiRequest(`${routesFor(role).sessions}/${sessionId}/close`, {
    method: 'POST',
  });
}

export function renameSession(role, sessionId, title) {
  return apiRequest(`${routesFor(role).sessions}/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  });
}

export function setSessionArchived(role, sessionId, archived) {
  const action = archived ? 'archive' : 'restore';
  return apiRequest(`${routesFor(role).sessions}/${sessionId}/${action}`, {
    method: 'POST',
  });
}

/** One UUID represents one user action and is never reused for a later turn. */
export function sendMessage(role, sessionId, message, clientMessageId = crypto.randomUUID()) {
  // Only patient appointment chat needs local calendar interpretation. The
  // backend validates this IANA identifier and uses it for phrases such as
  // "tomorrow"; administrators retain their separate strict request schema.
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  const body = {
    session_id: sessionId,
    client_message_id: clientMessageId,
    message,
  };
  if (role !== 'admin') body.timezone = timezone;
  return apiRequest(routesFor(role).chat, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** Confirmation is a separate typed turn, exactly as required by FastAPI. */
export function sendConfirmation(
  role,
  sessionId,
  actionId,
  approved,
  clientMessageId = crypto.randomUUID(),
) {
  return apiRequest(routesFor(role).chat, {
    method: 'POST',
    body: JSON.stringify({
      session_id: sessionId,
      client_message_id: clientMessageId,
      confirmation: { action_id: actionId, approved },
    }),
  });
}
