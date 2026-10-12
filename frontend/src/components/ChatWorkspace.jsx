import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../auth/authState';
import {
  closeSession,
  createSession,
  listMessages,
  listSessions,
  renameSession,
  sendConfirmation,
  sendMessage,
  setSessionArchived,
} from '../lib/chatApi';
import { logError } from '../lib/logger';
import { AppShell } from './AppShell';
import { AuditDrawer } from './AuditDrawer';

function friendlyDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

function sessionLabel(session) {
  return session.title || (session.subject_patient_id ? 'Patient invitation' : 'New conversation');
}

function responseMessageType(action) {
  if (action === 'confirmation_required') return 'confirmation_request';
  if (
    [
      'updated',
      'cancelled',
      'appointment_booked',
      'appointment_rescheduled',
      'appointment_cancelled',
    ].includes(action)
  )
    return 'confirmation_result';
  if (action === 'answer') return 'profile_result';
  if (action === 'information_required') return 'information_request';
  if (action === 'patient_invited') return 'invitation_result';
  return 'text';
}

function isDefaultTitle(role, title) {
  return !title || title === (role === 'admin' ? 'New patient invitation' : 'New conversation');
}

/** Create useful, privacy-conscious titles without another model request. */
function automaticTitle(role, message) {
  if (role === 'admin') return 'Patient invitation';
  const text = message.toLowerCase();
  if (text.includes('emergency')) return 'Emergency contact';
  if (text.includes('address'))
    return text.includes('update') ? 'Update address' : 'Profile address';
  if (text.includes('phone'))
    return text.includes('update') ? 'Update phone number' : 'Phone number';
  if (text.includes('email')) return 'Email information';
  if (text.includes('date of birth') || text.includes('dob')) return 'Date of birth';
  if (text.includes('name')) return 'Profile name';
  if (text.includes('appointment') || text.includes('book') || text.includes('reschedule'))
    return 'Appointments';
  return 'Profile information';
}

function previousUserMessage(messages, index) {
  for (let position = index - 1; position >= 0; position -= 1) {
    if (messages[position].role === 'user') return messages[position].content;
  }
  return null;
}

function ErrorNotice({ error, onRetry }) {
  if (!error) return null;
  return (
    <div className="workspace-error" role="alert">
      <div>
        <strong>We could not complete that request.</strong>
        <p>{error.message}</p>
        {error.requestId && <small>Request ID: {error.requestId}</small>}
      </div>
      {onRetry && (
        <button className="button button--quiet" type="button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

function DetailGrid({ data }) {
  if (!data || typeof data !== 'object') return null;
  const details =
    data.proposed_change || data.proposed_patient || data.proposed_appointment_action || data;
  if (Array.isArray(details.appointments) || Array.isArray(details.available_slots)) {
    const items = details.appointments || details.available_slots;
    if (!items.length) return null;
    return (
      <div className="appointment-list">
        {items.map((item) => (
          <article className="appointment-card" key={item.id}>
            <strong>{item.doctor?.display_name || 'Available appointment'}</strong>
            <span>{item.doctor?.specialty}</span>
            <time dateTime={item.starts_at}>{friendlyDate(item.starts_at)}</time>
            <small>{item.location_type?.replaceAll('_', ' ')}</small>
            <code>{item.id}</code>
          </article>
        ))}
      </div>
    );
  }
  const entries = Object.entries(details).filter(([, value]) => value !== null && value !== '');
  if (!entries.length) return null;
  return (
    <dl className="message-details">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key.replaceAll('_', ' ')}</dt>
          <dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Message({ message, onRetry, onRequestAgain }) {
  const isUser = message.role === 'user';
  const structured = message.structured_data;
  const data = structured?.data;
  const isProposal = message.message_type === 'confirmation_request';
  return (
    <article className={`message ${isUser ? 'message--user' : 'message--assistant'}`}>
      <div className="message__meta">
        <span>{isUser ? 'You' : 'Careline'}</span>
        <time dateTime={message.created_at}>{friendlyDate(message.created_at)}</time>
      </div>
      <div className="message__bubble">
        <p>{message.content}</p>
        {message.delivery_state === 'pending' && (
          <p className="message__delivery">Sending securely…</p>
        )}
        {message.delivery_state === 'failed' && (
          <div className="message__failed">
            <span>Message was not completed.</span>
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          </div>
        )}
        {!isUser && <DetailGrid data={data} />}
        {isProposal && (
          <div className="message__history-note">
            <span>Historical proposal · confirmation is no longer available</span>
            {onRequestAgain && (
              <button type="button" onClick={onRequestAgain}>
                Request again
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

function LiveAction({ action, busy, onDecision, onRequestAgain }) {
  if (!action) return null;
  return (
    <section className="action-card" aria-live="polite">
      <div className="action-card__icon">✓</div>
      <div className="action-card__body">
        <span className="action-card__eyebrow">
          {action.expired ? 'Confirmation expired' : 'Your approval is required'}
        </span>
        <h3>
          {action.role === 'admin'
            ? 'Send this patient invitation?'
            : action.data?.proposed_appointment_action
              ? 'Apply this appointment action?'
              : 'Save this profile change?'}
        </h3>
        <DetailGrid data={action.data} />
        <p>
          {action.expired
            ? 'This one-time confirmation is no longer valid. Request it again to continue safely.'
            : 'Review the information carefully. Nothing changes until you confirm.'}
        </p>
        <div className="action-card__buttons">
          {action.expired ? (
            <button
              className="button button--primary"
              type="button"
              disabled={busy}
              onClick={onRequestAgain}
            >
              Request again
            </button>
          ) : (
            <>
              <button
                className="button button--primary"
                type="button"
                disabled={busy}
                onClick={() => onDecision(true)}
              >
                {busy
                  ? 'Working…'
                  : action.role === 'admin'
                    ? 'Confirm & invite'
                    : action.data?.proposed_appointment_action
                      ? 'Confirm appointment'
                      : 'Confirm change'}
              </button>
              <button
                className="button button--quiet"
                type="button"
                disabled={busy}
                onClick={() => onDecision(false)}
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

export function ChatWorkspace({ role }) {
  const { user } = useAuth();
  const [sessions, setSessions] = useState([]);
  const [sessionsCursor, setSessionsCursor] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [messagesCursor, setMessagesCursor] = useState(null);
  const [draft, setDraft] = useState('');
  const [liveAction, setLiveAction] = useState(null);
  const [error, setError] = useState(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [working, setWorking] = useState(false);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [isOnline, setIsOnline] = useState(() => navigator.onLine);
  const [thinking, setThinking] = useState(false);
  const [failedTurn, setFailedTurn] = useState(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const endRef = useRef(null);
  const selectedIdRef = useRef(null);
  const messageLoadSequence = useRef(0);

  const selected = sessions.find((item) => item.id === selectedId) || null;
  const filteredSessions = sessions.filter((session) => {
    const matchesStatus = statusFilter === 'all' || session.status === statusFilter;
    const searchable = `${sessionLabel(session)} ${session.summary || ''}`.toLowerCase();
    return matchesStatus && searchable.includes(search.trim().toLowerCase());
  });

  function draftStorageKey(sessionId) {
    return `careline:draft:${user?.id || 'unknown'}:${role}:${sessionId}`;
  }

  async function loadSessionList({ append = false, cursor = null } = {}) {
    try {
      setError(null);
      const page = await listSessions(role, cursor);
      setSessions((current) => (append ? [...current, ...page.items] : page.items));
      setSessionsCursor(page.next_cursor);
      if (!append) setSelectedId((current) => current || page.items[0]?.id || null);
    } catch (error_) {
      logError('chat_sessions_load_failed', error_);
      setError(error_);
    } finally {
      setLoadingSessions(false);
    }
  }

  async function loadMessageList(sessionId, { append = false, cursor = null } = {}) {
    if (!sessionId) return;
    const loadSequence = ++messageLoadSequence.current;
    setLoadingMessages(true);
    try {
      setError(null);
      const page = await listMessages(role, sessionId, cursor);
      // Ignore a slow response for a session the user has already left. This
      // prevents rapid sidebar switching from showing the wrong conversation.
      if (loadSequence !== messageLoadSequence.current || selectedIdRef.current !== sessionId) {
        return;
      }
      // newest_first pagination returns earlier messages on the next page, so
      // subsequent pages belong before the already visible recent messages.
      setMessages((current) => (append ? [...page.items, ...current] : page.items));
      setMessagesCursor(page.next_cursor);
    } catch (error_) {
      logError('chat_messages_load_failed', error_);
      setError(error_);
    } finally {
      if (loadSequence === messageLoadSequence.current) setLoadingMessages(false);
    }
  }

  useEffect(() => {
    loadSessionList();
    // Role changes remount this route in normal navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [role]);

  useEffect(() => {
    function updateConnection() {
      setIsOnline(navigator.onLine);
    }
    window.addEventListener('online', updateConnection);
    window.addEventListener('offline', updateConnection);
    return () => {
      window.removeEventListener('online', updateConnection);
      window.removeEventListener('offline', updateConnection);
    };
  }, []);

  useEffect(() => {
    selectedIdRef.current = selectedId;
    messageLoadSequence.current += 1;
    setMessages([]);
    setMessagesCursor(null);
    setLiveAction(null);
    setFailedTurn(null);
    setEditingTitle(false);
    setDraft(selectedId ? sessionStorage.getItem(draftStorageKey(selectedId)) || '' : '');
    if (selectedId) loadMessageList(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  useEffect(() => {
    if (!loadingMessages) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, liveAction, loadingMessages]);

  async function handleNewSession() {
    setWorking(true);
    try {
      setError(null);
      const created = await createSession(role);
      setSessions((current) => [created, ...current]);
      setSelectedId(created.id);
    } catch (error_) {
      logError('chat_session_create_failed', error_);
      setError(error_);
    } finally {
      setWorking(false);
    }
  }

  async function submitMessageTurn(
    message,
    {
      clientMessageId = crypto.randomUUID(),
      optimisticId = `local-${crypto.randomUUID()}`,
      reuseOptimistic = false,
      clearDraft = true,
    } = {},
  ) {
    if (!message || !selectedId || working || !isOnline) return;
    setWorking(true);
    setThinking(true);
    const submittedSessionId = selectedId;
    // Show the patient's question immediately instead of leaving an apparently
    // empty timeline while the model and database finish the turn.
    setMessages((current) => {
      const optimistic = {
        id: optimisticId,
        session_id: submittedSessionId,
        sequence_number: Number.MAX_SAFE_INTEGER,
        role: 'user',
        message_type: 'text',
        content: message,
        structured_data: null,
        created_at: new Date().toISOString(),
        delivery_state: 'pending',
      };
      return reuseOptimistic
        ? current.map((item) => (item.id === optimisticId ? optimistic : item))
        : [...current, optimistic];
    });
    try {
      setError(null);
      const response = await sendMessage(role, submittedSessionId, message, clientMessageId);
      if (selectedIdRef.current !== submittedSessionId) return;
      // The immediate response makes the completed answer visible without an
      // extra round trip. The following history reload replaces both local
      // entries with their authoritative database records.
      setMessages((current) => [
        ...current,
        {
          id: response.turn?.output_message_id || `local-${crypto.randomUUID()}`,
          session_id: submittedSessionId,
          sequence_number: Number.MAX_SAFE_INTEGER,
          role: 'assistant',
          message_type: responseMessageType(response.action),
          content: response.message,
          structured_data: response,
          created_at: new Date().toISOString(),
        },
      ]);
      if (response.requires_confirmation && response.pending_action_id) {
        setLiveAction({
          id: response.pending_action_id,
          data: response.data,
          role,
          sourceMessage: message,
        });
      }
      if (selected && isDefaultTitle(role, selected.title)) {
        try {
          const renamed = await renameSession(
            role,
            submittedSessionId,
            automaticTitle(role, message),
          );
          setSessions((current) =>
            current.map((item) => (item.id === renamed.id ? renamed : item)),
          );
        } catch (titleError) {
          // A title is convenience metadata. Never turn a successful chat
          // response into a failed user action when only automatic naming fails.
          logError('chat_session_auto_title_failed', titleError);
        }
      }
      await loadMessageList(submittedSessionId);
      await loadSessionList();
      setFailedTurn(null);
      if (clearDraft) {
        setDraft('');
        sessionStorage.removeItem(draftStorageKey(submittedSessionId));
      }
    } catch (error_) {
      logError('chat_message_send_failed', error_);
      if (error_?.code === 'chat_turn_already_processed') {
        // The first request completed but its response was lost. Reload the
        // durable result instead of generating a second action.
        await loadMessageList(submittedSessionId);
        setFailedTurn(null);
        setError(null);
        if (clearDraft) {
          setDraft('');
          sessionStorage.removeItem(draftStorageKey(submittedSessionId));
        }
      } else {
        setMessages((current) =>
          current.map((item) =>
            item.id === optimisticId ? { ...item, delivery_state: 'failed' } : item,
          ),
        );
        setFailedTurn({ message, clientMessageId, optimisticId, sessionId: submittedSessionId });
        setError(error_);
      }
    } finally {
      setThinking(false);
      setWorking(false);
    }
  }

  async function handleSubmit(event) {
    event.preventDefault();
    await submitMessageTurn(draft.trim());
  }

  function handleRetry() {
    if (!failedTurn || failedTurn.sessionId !== selectedId) return;
    submitMessageTurn(failedTurn.message, {
      clientMessageId: failedTurn.clientMessageId,
      optimisticId: failedTurn.optimisticId,
      reuseOptimistic: true,
    });
  }

  function handleRequestAgain(message) {
    if (!message) return;
    setLiveAction(null);
    submitMessageTurn(message, { clearDraft: false });
  }

  async function handleDecision(approved) {
    if (!liveAction || !selectedId) return;
    setWorking(true);
    const confirmationClientId = liveAction.confirmationClientId || crypto.randomUUID();
    setLiveAction((current) => current && { ...current, confirmationClientId });
    try {
      setError(null);
      await sendConfirmation(role, selectedId, liveAction.id, approved, confirmationClientId);
      setLiveAction(null);
      await loadMessageList(selectedId);
      await loadSessionList();
    } catch (error_) {
      logError('chat_confirmation_failed', error_);
      if (error_?.code === 'chat_turn_already_processed') {
        setLiveAction(null);
        await loadMessageList(selectedId);
      } else if (
        error_?.code === 'chat_action_invalid' ||
        error_?.code === 'admin_chat_action_invalid'
      ) {
        setLiveAction((current) => current && { ...current, expired: true });
        setError(null);
      } else {
        setError(error_);
      }
    } finally {
      setWorking(false);
    }
  }

  async function handleClose() {
    if (!selectedId || working) return;
    setWorking(true);
    try {
      setError(null);
      const closed = await closeSession(role, selectedId);
      setSessions((current) => current.map((item) => (item.id === closed.id ? closed : item)));
      setLiveAction(null);
    } catch (error_) {
      logError('chat_session_close_failed', error_);
      setError(error_);
    } finally {
      setWorking(false);
    }
  }

  async function handleRename(event) {
    event.preventDefault();
    const title = titleDraft.trim();
    if (!selectedId || !title || working) return;
    setWorking(true);
    try {
      setError(null);
      const renamed = await renameSession(role, selectedId, title);
      setSessions((current) => current.map((item) => (item.id === renamed.id ? renamed : item)));
      setEditingTitle(false);
    } catch (error_) {
      logError('chat_session_rename_failed', error_);
      setError(error_);
    } finally {
      setWorking(false);
    }
  }

  async function handleArchiveState(archived) {
    if (!selectedId || working) return;
    setWorking(true);
    try {
      setError(null);
      const changed = await setSessionArchived(role, selectedId, archived);
      setSessions((current) => current.map((item) => (item.id === changed.id ? changed : item)));
    } catch (error_) {
      logError('chat_session_archive_failed', error_);
      setError(error_);
    } finally {
      setWorking(false);
    }
  }

  const prompt =
    role === 'admin'
      ? 'Describe the patient you want to invite…'
      : 'Ask about your profile or appointments…';
  const latestProposalIndex = messages.findLastIndex(
    (message) => message.message_type === 'confirmation_request',
  );
  const proposalResolved =
    latestProposalIndex >= 0 &&
    messages
      .slice(latestProposalIndex + 1)
      .some((message) =>
        ['confirmation_result', 'invitation_result'].includes(message.message_type),
      );

  return (
    <AppShell role={role}>
      <main className="chat-layout">
        <aside className="session-panel" aria-label="Conversation history">
          <div className="session-panel__heading">
            <div>
              <p className="eyebrow">Careline assistant</p>
              <h1>Conversations</h1>
            </div>
            <button
              className="new-chat-button"
              type="button"
              disabled={working}
              onClick={handleNewSession}
              aria-label="Start a new conversation"
            >
              +
            </button>
          </div>

          <div className="session-list">
            <div className="session-tools">
              <label className="session-search">
                <span className="sr-only">Search conversations</span>
                <input
                  type="search"
                  value={search}
                  placeholder="Search conversations"
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <label>
                <span className="sr-only">Filter conversations by status</span>
                <select
                  value={statusFilter}
                  onChange={(event) => setStatusFilter(event.target.value)}
                >
                  <option value="all">All</option>
                  <option value="active">Active</option>
                  <option value="closed">Closed</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
            </div>
            {loadingSessions && <p className="panel-note">Loading conversations…</p>}
            {!loadingSessions && sessions.length === 0 && (
              <div className="empty-sessions">
                <strong>No conversations yet</strong>
                <p>Start one when you are ready.</p>
              </div>
            )}
            {!loadingSessions && sessions.length > 0 && filteredSessions.length === 0 && (
              <p className="panel-note">No conversations match this search.</p>
            )}
            {filteredSessions.map((session) => (
              <button
                className={`session-item ${session.id === selectedId ? 'session-item--active' : ''}`}
                type="button"
                key={session.id}
                onClick={() => setSelectedId(session.id)}
              >
                <span className="session-item__icon">{role === 'admin' ? 'A' : 'P'}</span>
                <span className="session-item__copy">
                  <strong>{sessionLabel(session)}</strong>
                  <small>
                    {session.summary ||
                      (session.status === 'active' ? 'Active conversation' : 'Closed conversation')}
                  </small>
                </span>
                <time>{friendlyDate(session.last_message_at)}</time>
              </button>
            ))}
            {sessionsCursor && (
              <button
                className="load-more"
                type="button"
                onClick={() => loadSessionList({ append: true, cursor: sessionsCursor })}
              >
                Load more conversations
              </button>
            )}
          </div>
        </aside>

        <section className="conversation-panel">
          {selected ? (
            <>
              <header className="conversation-header">
                <div>
                  <div className="conversation-header__title">
                    <span className={`status-dot status-dot--${selected.status}`} />
                    {editingTitle ? (
                      <form className="title-editor" onSubmit={handleRename}>
                        <input
                          autoFocus
                          maxLength="200"
                          value={titleDraft}
                          onChange={(event) => setTitleDraft(event.target.value)}
                          aria-label="Conversation title"
                        />
                        <button type="submit" disabled={!titleDraft.trim() || working}>
                          Save
                        </button>
                        <button type="button" onClick={() => setEditingTitle(false)}>
                          Cancel
                        </button>
                      </form>
                    ) : (
                      <>
                        <h2>{sessionLabel(selected)}</h2>
                        <button
                          className="title-edit-button"
                          type="button"
                          onClick={() => {
                            setTitleDraft(sessionLabel(selected));
                            setEditingTitle(true);
                          }}
                          aria-label="Rename conversation"
                        >
                          Rename
                        </button>
                      </>
                    )}
                  </div>
                  <p>
                    {selected.status === 'active'
                      ? 'Secure conversation'
                      : 'This conversation is closed'}
                    {role === 'admin' && selected.subject_patient_id
                      ? ` · Patient linked: ${selected.subject_patient_id}`
                      : ''}
                  </p>
                </div>
                <div className="conversation-actions">
                  {role === 'admin' && (
                    <button
                      className="button button--quiet"
                      type="button"
                      onClick={() => setAuditOpen(true)}
                    >
                      Audit details
                    </button>
                  )}
                  {selected.status === 'active' && (
                    <button
                      className="button button--quiet"
                      type="button"
                      disabled={working}
                      onClick={handleClose}
                    >
                      Close conversation
                    </button>
                  )}
                  {selected.status === 'closed' && (
                    <button
                      className="button button--quiet"
                      type="button"
                      disabled={working}
                      onClick={() => handleArchiveState(true)}
                    >
                      Archive
                    </button>
                  )}
                  {selected.status === 'archived' && (
                    <button
                      className="button button--quiet"
                      type="button"
                      disabled={working}
                      onClick={() => handleArchiveState(false)}
                    >
                      Restore
                    </button>
                  )}
                </div>
              </header>

              {!isOnline && (
                <div className="connection-banner" role="status">
                  You are offline. Your draft is safe in this browser and can be sent after you
                  reconnect.
                </div>
              )}

              <div className="message-scroll">
                {messagesCursor && (
                  <button
                    className="load-more"
                    type="button"
                    disabled={loadingMessages}
                    onClick={() =>
                      loadMessageList(selectedId, { append: true, cursor: messagesCursor })
                    }
                  >
                    Load more messages
                  </button>
                )}
                {loadingMessages && messages.length === 0 && (
                  <p className="panel-note">Loading messages…</p>
                )}
                {!loadingMessages && messages.length === 0 && (
                  <div className="conversation-empty">
                    <span className="conversation-empty__mark">✦</span>
                    <h3>
                      {role === 'admin'
                        ? 'Invite a patient with Careline'
                        : 'How can Careline help?'}
                    </h3>
                    <p>
                      {role === 'admin'
                        ? 'Provide the full name, date of birth, phone number, and email. You will review everything before an invitation is sent.'
                        : 'Ask about your profile or appointments. Profile and appointment changes always require your confirmation.'}
                    </p>
                  </div>
                )}
                {messages.map((message, index) => {
                  const canRecoverProposal =
                    !liveAction &&
                    index === latestProposalIndex &&
                    !proposalResolved &&
                    message.message_type === 'confirmation_request';
                  const sourceMessage = canRecoverProposal
                    ? previousUserMessage(messages, index)
                    : null;
                  return (
                    <Message
                      key={message.id}
                      message={message}
                      onRetry={failedTurn?.optimisticId === message.id ? handleRetry : undefined}
                      onRequestAgain={
                        sourceMessage ? () => handleRequestAgain(sourceMessage) : undefined
                      }
                    />
                  );
                })}
                {thinking && (
                  <article className="thinking-message" aria-live="polite">
                    <span />
                    <span />
                    <span />
                    <p>Careline is thinking…</p>
                  </article>
                )}
                <LiveAction
                  action={liveAction}
                  busy={working}
                  onDecision={handleDecision}
                  onRequestAgain={() => handleRequestAgain(liveAction?.sourceMessage)}
                />
                <ErrorNotice error={error} />
                <div ref={endRef} />
              </div>

              <form className="composer" onSubmit={handleSubmit}>
                <label className="sr-only" htmlFor="chat-message">
                  Message Careline
                </label>
                <textarea
                  id="chat-message"
                  rows="1"
                  maxLength={role === 'admin' ? 3000 : 2000}
                  value={draft}
                  disabled={
                    selected.status !== 'active' || working || Boolean(liveAction) || !isOnline
                  }
                  placeholder={liveAction ? 'Confirm or cancel the action above first' : prompt}
                  onChange={(event) => {
                    const value = event.target.value;
                    setDraft(value);
                    if (selectedId) {
                      if (value) sessionStorage.setItem(draftStorageKey(selectedId), value);
                      else sessionStorage.removeItem(draftStorageKey(selectedId));
                    }
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      event.currentTarget.form?.requestSubmit();
                    }
                  }}
                />
                <button
                  className="send-button"
                  type="submit"
                  disabled={
                    !draft.trim() ||
                    selected.status !== 'active' ||
                    working ||
                    Boolean(liveAction) ||
                    !isOnline
                  }
                  aria-label="Send message"
                >
                  ↑
                </button>
                <small>Enter to send · Shift + Enter for a new line</small>
              </form>
            </>
          ) : (
            <div className="no-session">
              <span>✦</span>
              <h2>Start a secure conversation</h2>
              <p>Your messages and agent actions will be recorded in your private chat history.</p>
              <button
                className="button button--primary"
                type="button"
                disabled={working}
                onClick={handleNewSession}
              >
                Start conversation
              </button>
              <ErrorNotice error={error} onRetry={() => loadSessionList()} />
            </div>
          )}
        </section>
      </main>
      {role === 'admin' && auditOpen && (
        <AuditDrawer initialSessionId={selectedId} onClose={() => setAuditOpen(false)} />
      )}
    </AppShell>
  );
}
