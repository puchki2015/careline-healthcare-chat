export function Brand({ compact = false }) {
  return (
    <div className={`brand${compact ? ' brand--compact' : ''}`} aria-label="Careline">
      <span className="brand__mark" aria-hidden="true">
        <svg viewBox="0 0 32 32">
          <path d="M16 27.2S5.2 21 5.2 12.5A6.2 6.2 0 0 1 16 8.3a6.2 6.2 0 0 1 10.8 4.2C26.8 21 16 27.2 16 27.2Z" />
          <path d="M10.2 16h3.2l1.5-3.4 2.2 6.4 1.7-3h3" />
        </svg>
      </span>
      <span className="brand__name">Careline</span>
    </div>
  );
}
