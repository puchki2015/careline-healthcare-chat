import { AppShell } from '../components/AppShell';

export function WorkspacePlaceholder({ role }) {
  return (
    <AppShell role={role}>
      <main className="workspace-placeholder">
        <div className="workspace-placeholder__copy">
          <p className="eyebrow">Authentication ready</p>
          <h1>{role === 'admin' ? 'Administrator workspace' : 'Patient workspace'}</h1>
          <p>
            Your secure session, protected routing, and authenticated API connection are working.
            The conversation interface comes next.
          </p>
          <div className="readiness-card">
            <span className="readiness-card__check">✓</span>
            <div>
              <strong>Step 1 complete</strong>
              <p>Careline restored your Supabase session and confirmed your workspace access.</p>
            </div>
          </div>
        </div>
      </main>
    </AppShell>
  );
}
