import { useEffect, useMemo, useState } from 'react';
import { logError, logEvent } from '../lib/logger';
import { supabase } from '../lib/supabase';
import { AuthContext } from './authState';

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    supabase.auth.getSession().then(({ data, error }) => {
      if (!mounted) return;
      if (error) logError('auth_session_restore_failed', error);
      setSession(data.session ?? null);
      setIsLoading(false);
      logEvent('auth_session_restored', { authenticated: Boolean(data.session) });
    });

    const { data } = supabase.auth.onAuthStateChange((event, nextSession) => {
      if (!mounted) return;
      setSession(nextSession);
      setIsLoading(false);
      logEvent('auth_state_changed', { event });
    });

    return () => {
      mounted = false;
      data.subscription.unsubscribe();
    };
  }, []);

  const value = useMemo(
    () => ({
      session,
      user: session?.user ?? null,
      isLoading,
      async signIn(email, password) {
        if (!supabase) throw new Error('Frontend configuration is missing');
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        logEvent('auth_sign_in_succeeded');
      },
      async signOut() {
        if (!supabase) return;
        const { error } = await supabase.auth.signOut();
        if (error) throw error;
        logEvent('auth_sign_out_succeeded');
      },
      async updatePassword(password) {
        if (!supabase) throw new Error('Frontend configuration is missing');
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        logEvent('auth_password_updated');
      },
    }),
    [isLoading, session],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
