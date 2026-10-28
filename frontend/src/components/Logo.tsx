/** LedgerPic mark: a receipt with a scan line across it. */
function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="lp-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#10b981" />
          <stop offset="1" stopColor="#0b6e57" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="url(#lp-g)" />
      <path
        d="M10 7.5h12a1 1 0 0 1 1 1v15.2l-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2-2 1.2-2-1.2V8.5a1 1 0 0 1 1-1Z"
        fill="#fff"
      />
      <rect x="12" y="11" width="8" height="1.6" rx=".8" fill="#0f9d77" opacity=".55" />
      <rect x="12" y="18" width="5" height="1.6" rx=".8" fill="#0f9d77" opacity=".55" />
      <rect x="6.5" y="14.2" width="19" height="1.8" rx=".9" fill="#a7f3d0" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="brand">
      <LogoMark />
      <span className="brand-word">
        Ledger<span>Pic</span>
      </span>
    </span>
  );
}
