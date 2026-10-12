import { createClient } from '@supabase/supabase-js';
import { runtimeConfig } from './runtimeConfig';

/**
 * A single browser client owns login persistence and token refresh. Supabase
 * stores the short-lived session in browser storage; application components
 * consume it through AuthProvider rather than creating additional clients.
 */
export const supabase = runtimeConfig
  ? createClient(runtimeConfig.supabaseUrl, runtimeConfig.supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;
