/**
 * Read and validate browser-safe runtime settings in one place.
 *
 * Vite deliberately exposes only variables beginning with VITE_. The Supabase
 * anon key is safe for browser use because database access is still protected
 * by RLS. The service-role key must never be added here.
 */
const rawConfig = {
  supabaseUrl: import.meta.env.VITE_SUPABASE_URL?.trim(),
  supabaseAnonKey: import.meta.env.VITE_SUPABASE_ANON_KEY?.trim(),
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL?.trim(),
};

const missingVariables = [
  !rawConfig.supabaseUrl && 'VITE_SUPABASE_URL',
  !rawConfig.supabaseAnonKey && 'VITE_SUPABASE_ANON_KEY',
  !rawConfig.apiBaseUrl && 'VITE_API_BASE_URL',
].filter(Boolean);

export const configurationError = missingVariables.length
  ? `Missing frontend configuration: ${missingVariables.join(', ')}`
  : null;

export const runtimeConfig = configurationError
  ? null
  : Object.freeze({
      ...rawConfig,
      // Avoid accidental double slashes when API paths are appended.
      apiBaseUrl: rawConfig.apiBaseUrl.replace(/\/$/, ''),
    });
