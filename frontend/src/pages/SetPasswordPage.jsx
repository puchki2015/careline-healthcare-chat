import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/authState';
import { Brand } from '../components/Brand';
import { FullPageLoader } from '../components/FullPageLoader';
import { logError } from '../lib/logger';

export function SetPasswordPage() {
  const { session, isLoading, updatePassword } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (isLoading) return <FullPageLoader label="Verifying your invitation" />;
  if (!session) return <Navigate to="/login" replace />;

  async function handleSubmit(event) {
    event.preventDefault();
    setErrorMessage('');
    if (password.length < 8) {
      setErrorMessage('Use at least 8 characters for your password.');
      return;
    }
    if (password !== confirmation) {
      setErrorMessage('The passwords do not match.');
      return;
    }
    setIsSubmitting(true);
    try {
      await updatePassword(password);
      navigate('/patient/chat', { replace: true });
    } catch (error) {
      logError('auth_password_update_failed', error);
      setErrorMessage('Your password could not be saved. The invitation may have expired.');
      setIsSubmitting(false);
    }
  }

  return (
    <main className="simple-auth-layout">
      <div className="simple-auth-card">
        <Brand />
        <div>
          <p className="eyebrow">Invitation accepted</p>
          <h1>Create your password</h1>
          <p className="muted">
            Choose a secure password to finish setting up your patient account.
          </p>
        </div>
        <form className="form-stack" onSubmit={handleSubmit}>
          <label className="field">
            <span>New password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          <label className="field">
            <span>Confirm new password</span>
            <input
              type="password"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </label>
          {errorMessage && <div className="form-alert">{errorMessage}</div>}
          <button className="button button--primary button--wide" disabled={isSubmitting}>
            {isSubmitting ? 'Saving password…' : 'Finish account setup'}
          </button>
        </form>
      </div>
    </main>
  );
}
