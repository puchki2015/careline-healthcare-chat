import { useEffect, useMemo, useState } from 'react';
import { getSessionAudit, listAuditSessions } from '../lib/auditApi';
import { logError } from '../lib/logger';

function displayDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function DataBlock({ title, items, empty = 'No records' }) {
  return (
    <section className="audit-section">
      <h3>
        {title} <span>{items.length}</span>
      </h3>
      {items.length === 0 ? (
        <p className="audit-empty">{empty}</p>
      ) : (
        items.map((item, index) => (
          <details key={item.id || `${title}-${index}`}>
            <summary>
              {item.intent ||
                item.tool_name ||
                item.action_type ||
                item.message_type ||
                item.source_title ||
                `Record ${index + 1}`}
            </summary>
            <pre>{JSON.stringify(item, null, 2)}</pre>
          </details>
        ))
      )}
    </section>
  );
}

export function AuditDrawer({ initialSessionId, onClose }) {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(initialSessionId || '');
  const [search, setSearch] = useState('');
  const [bundle, setBundle] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    listAuditSessions()
      .then((page) => {
        setSessions(page.items);
        setSelectedId((current) => current || page.items[0]?.id || '');
      })
      .catch((error_) => {
        logError('admin_audit_sessions_failed', error_);
        setError(error_);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    getSessionAudit(selectedId)
      .then(setBundle)
      .catch((error_) => {
        logError('admin_audit_bundle_failed', error_);
        setError(error_);
      })
      .finally(() => setLoading(false));
  }, [selectedId]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return sessions;
    return sessions.filter((session) =>
      `${session.id} ${session.title || ''} ${session.chat_initiator_role} ${session.subject_patient_id || ''}`
        .toLowerCase()
        .includes(term),
    );
  }, [search, sessions]);

  function downloadAudit() {
    if (!bundle) return;
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `careline-audit-${bundle.session.id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className="audit-overlay"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <aside
        className="audit-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Administrative chat audit"
      >
        <header className="audit-header">
          <div>
            <p className="eyebrow">Administrator only</p>
            <h2>Chat audit</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close audit">
            ×
          </button>
        </header>
        <div className="audit-picker">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search session, role, or patient ID"
            aria-label="Search audit sessions"
          />
          <select
            value={selectedId}
            onChange={(event) => {
              setLoading(true);
              setError(null);
              setSelectedId(event.target.value);
            }}
            aria-label="Audit session"
          >
            {filtered.map((session) => (
              <option key={session.id} value={session.id}>
                {session.title || 'Untitled'} · {session.chat_initiator_role}
              </option>
            ))}
          </select>
        </div>
        {loading && <p className="audit-loading">Loading sanitized audit details…</p>}
        {error && (
          <div className="workspace-error">
            <div>
              <strong>Audit unavailable</strong>
              <p>{error.message}</p>
            </div>
          </div>
        )}
        {!loading && bundle && (
          <div className="audit-content">
            <section className="audit-overview">
              <div>
                <span>Session</span>
                <strong>{bundle.session.id}</strong>
              </div>
              <div>
                <span>Initiated by</span>
                <strong>{bundle.session.chat_initiator_role}</strong>
              </div>
              <div>
                <span>Patient</span>
                <strong>{bundle.session.subject_patient_id || 'Not linked'}</strong>
              </div>
              <div>
                <span>Status</span>
                <strong>{bundle.session.status}</strong>
              </div>
            </section>
            <button
              className="button button--quiet audit-download"
              type="button"
              onClick={downloadAudit}
            >
              Download sanitized JSON
            </button>
            <section className="audit-section audit-summaries">
              <h3>
                Last 3 patient summaries <span>{bundle.patient_summaries.length}</span>
              </h3>
              {bundle.patient_summaries.length === 0 ? (
                <p className="audit-empty">No linked patient summaries are available.</p>
              ) : (
                bundle.patient_summaries.map((item) => (
                  <article key={item.id}>
                    <strong>{item.title || 'Closed conversation'}</strong>
                    <time>{displayDate(item.ended_at)}</time>
                    <p>{item.summary}</p>
                    <small>{item.id}</small>
                  </article>
                ))
              )}
            </section>
            <DataBlock title="Messages" items={bundle.messages} />
            <DataBlock title="LangGraph runs" items={bundle.runs} />
            <DataBlock title="Tool events" items={bundle.tool_events} />
            <DataBlock title="Pending actions" items={bundle.pending_actions} />
            <DataBlock title="Context sources" items={bundle.context_sources} />
          </div>
        )}
      </aside>
    </div>
  );
}
