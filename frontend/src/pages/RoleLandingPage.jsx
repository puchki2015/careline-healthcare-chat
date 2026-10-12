import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { FullPageLoader } from '../components/FullPageLoader';
import { ApiError, resolveLandingRole } from '../lib/apiClient';
import { logError } from '../lib/logger';

export function RoleLandingPage() {
  const [role, setRole] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    resolveLandingRole()
      .then((resolvedRole) => active && setRole(resolvedRole))
      .catch((error_) => {
        logError('role_resolution_failed', error_);
        if (active) setError(error_);
      });
    return () => {
      active = false;
    };
  }, []);

  if (role === 'admin') return <Navigate to="/admin/chat" replace />;
  if (role === 'patient') return <Navigate to="/patient/chat" replace />;
  if (!error) return <FullPageLoader label="Preparing your workspace" />;

  return (
    <main className="status-screen">
      <span className="status-icon status-icon--error">!</span>
      <h1>We could not open your workspace</h1>
      <p>{error instanceof ApiError ? error.message : 'Please try again in a moment.'}</p>
      {error?.requestId && <small>Request ID: {error.requestId}</small>}
      <button className="button button--primary" onClick={() => window.location.reload()}>
        Try again
      </button>
    </main>
  );
}
