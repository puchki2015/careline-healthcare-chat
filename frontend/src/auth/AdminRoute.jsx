import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { FullPageLoader } from '../components/FullPageLoader';
import { resolveLandingRole } from '../lib/apiClient';
import { logError } from '../lib/logger';

/**
 * Confirm UI authorization through FastAPI, never through a browser flag.
 * The backend remains the final authority for every administrator API call.
 */
export function AdminRoute({ children }) {
  const [result, setResult] = useState({ status: 'loading', role: null });

  useEffect(() => {
    let active = true;
    resolveLandingRole()
      .then((role) => active && setResult({ status: 'ready', role }))
      .catch((error) => {
        logError('admin_route_authorization_failed', error);
        if (active) setResult({ status: 'error', role: null });
      });
    return () => {
      active = false;
    };
  }, []);

  if (result.status === 'loading') {
    return <FullPageLoader label="Confirming administrator access" />;
  }
  if (result.role !== 'admin') return <Navigate to="/patient/chat" replace />;
  return children;
}
