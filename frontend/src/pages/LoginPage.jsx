import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/authState';
import { Brand } from '../components/Brand';
import { logError } from '../lib/logger';

function friendlyAuthMessage(error) {
  if (error?.status === 400 || error?.code === 'invalid_credentials') {
    return 'The email or password is incorrect.';
  }
  if (error?.status === 429) return 'Too many attempts. Please wait and try again.';
  return 'We could not sign you in. Please try again.';
}

export function LoginPage() {
  const { session, isLoading, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [errorMessage, setErrorMessage] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && session) navigate('/', { replace: true });
  }, [isLoading, navigate, session]);

  async function handleSubmit(event) {
    event.preventDefault();
    setErrorMessage('');
    setIsSubmitting(true);
    try {
      await signIn(email.trim(), password);
      navigate(location.state?.from || '/', { replace: true });
    } catch (error) {
      logError('auth_sign_in_failed', error);
      setErrorMessage(friendlyAuthMessage(error));
      setIsSubmitting(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-story" aria-label="About Careline">
        <Brand />
        <div className="auth-story__content">
          <p className="eyebrow">Connected care, made human</p>
          <h1>Your care conversations, organized and secure.</h1>
          <p>
            One calm workspace for patients and care teams to ask questions, manage details, and
            keep every conversation in context.
          </p>
          <div className="trust-row" aria-label="Careline principles">
            <span>Private by design</span>
            <span>Role-aware access</span>
            <span>Clear confirmations</span>
          </div>
        </div>
        <div className="pulse-art" aria-hidden="true">
          <span className="pulse-art__orb" />
          <svg viewBox="0 0 620 180" preserveAspectRatio="none">
            <path d="M0 105h110l28-52 43 102 38-75 32 25h72l24-44 37 82 35-38h201" />
          </svg>
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-card">
          <div className="auth-card__heading">
            <p className="eyebrow">Welcome back</p>
            <h2>Sign in to Careline</h2>
            <p>Use the email and password connected to your account.</p>
          </div>

          <form onSubmit={handleSubmit} className="form-stack" noValidate>
            <label className="field">
              <span>Email address</span>
              <input
                type="email"
                name="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                autoComplete="email"
                placeholder="you@example.com"
                required
              />
            </label>
            <label className="field">
              <span>Password</span>
              <input
                type="password"
                name="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                placeholder="Enter your password"
                minLength={8}
                required
              />
            </label>

            {errorMessage && (
              <div className="form-alert" role="alert">
                {errorMessage}
              </div>
            )}

            <button className="button button--primary button--wide" disabled={isSubmitting}>
              {isSubmitting ? 'Signing in…' : 'Sign in securely'}
            </button>
          </form>

          <p className="auth-card__footnote">
            Need an account? Ask your Careline administrator for an invitation.
          </p>
        </div>
      </section>
    </main>
  );
}
