/**
 * Inline stroke icons. Owner: person 1.
 * Drawn here rather than pulled from an icon package: a dozen paths is less to
 * vendor than a library, and they inherit currentColor in both themes.
 */
import type { ReactNode } from 'react';

function Icon({ children, className = 'h-[18px] w-[18px]' }: {
  children: ReactNode; className?: string;
}) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7}
         strokeLinecap="round" strokeLinejoin="round" className={className}
         aria-hidden>
      {children}
    </svg>
  );
}

type P = { className?: string };

export const ChatIcon = (p: P) => <Icon {...p}>
  <path d="M12 3l1.9 4.6L18.5 9.5l-4.6 1.9L12 16l-1.9-4.6L5.5 9.5l4.6-1.9z" />
  <path d="M19 15l.8 1.9 1.9.8-1.9.8L19 20.4l-.8-1.9-1.9-.8 1.9-.8z" />
</Icon>;

export const DocIcon = (p: P) => <Icon {...p}>
  <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
  <path d="M14 3v5h5M9 13h6M9 17h4" />
</Icon>;

export const ChartIcon = (p: P) => <Icon {...p}>
  <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
</Icon>;

export const EditIcon = (p: P) => <Icon {...p}>
  <path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" />
  <path d="M18.4 2.6a2 2 0 0 1 3 3L12 15l-4 1 1-4z" />
</Icon>;

export const SunIcon = (p: P) => <Icon {...p}>
  <circle cx="12" cy="12" r="4" />
  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
</Icon>;

export const MoonIcon = (p: P) => <Icon {...p}>
  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
</Icon>;

export const BulbIcon = (p: P) => <Icon {...p}>
  <path d="M9 18h6M10 21h4" />
  <path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z" />
</Icon>;

export const PlusIcon = (p: P) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;

export const SendIcon = (p: P) => <Icon {...p}>
  <path d="M22 2L11 13M22 2l-7 20-4-9-9-4z" />
</Icon>;

export const ChevronIcon = (p: P) => <Icon {...p}><path d="M6 9l6 6 6-6" /></Icon>;

export const ShieldIcon = (p: P) => <Icon {...p}>
  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  <path d="M9 12l2 2 4-4" />
</Icon>;

/** The mark: a rounded green tile, as in the brand lockup. */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <span className="inline-flex shrink-0 items-center justify-center rounded-xl
                     bg-brand text-white shadow-sm"
          style={{ width: size, height: size }}>
      <svg viewBox="0 0 24 24" className="h-[58%] w-[58%]" fill="none"
           stroke="currentColor" strokeWidth={2.4} strokeLinecap="round"
           strokeLinejoin="round" aria-hidden>
        <path d="M7 20V5h6a4.5 4.5 0 0 1 0 9H7" />
      </svg>
    </span>
  );
}
