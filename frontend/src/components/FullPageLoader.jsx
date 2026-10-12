import { Brand } from './Brand';

export function FullPageLoader({ label = 'Loading Careline' }) {
  return (
    <main className="loading-screen" aria-live="polite" aria-busy="true">
      <Brand />
      <span className="loading-spinner" aria-hidden="true" />
      <p>{label}…</p>
    </main>
  );
}
