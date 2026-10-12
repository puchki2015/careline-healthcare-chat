import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/authState';
import { logError } from '../lib/logger';
import { Brand } from './Brand';

export function AppShell({ role, children }) {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [isSigningOut, setIsSigningOut] = useState(false);

  async function handleSignOut() {
    setIsSigningOut(true);
    try {
      await signOut();
      navigate('/login', { replace: true });
    } catch (error) {
      logError('auth_sign_out_failed', error);
      setIsSigningOut(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <Brand compact />
        <nav className="app-nav" aria-label="Main navigation">
          <NavLink to={role === 'admin' ? '/admin/chat' : '/patient/chat'}>Conversations</NavLink>
          {role === 'admin' && <NavLink to="/admin/appointments">Appointments</NavLink>}
        </nav>
        <div className="app-header__account">
          <span className="role-pill">{role}</span>
          <span className="account-email">{user?.email}</span>
          <button
            className="button button--quiet"
            type="button"
            disabled={isSigningOut}
            onClick={handleSignOut}
          >
            {isSigningOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </header>
      {children}
    </div>
  );
}
