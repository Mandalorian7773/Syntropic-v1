/**
 * Light / dark theme. Owner: person 1.
 *
 * The choice lives in localStorage and is applied as a `dark` class on <html>.
 * index.html applies it before first paint too, so a reload never flashes the
 * wrong theme; this store only has to keep the class in step with toggles.
 */
import { create } from 'zustand';

export type Theme = 'light' | 'dark';
const KEY = 'privis-theme';

function initial(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch { /* storage blocked: fall through to the system preference */ }
  return typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark' : 'light';
}

function apply(theme: Theme) {
  if (typeof document === 'undefined') return;
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

export const useTheme = create<{ theme: Theme; toggle: () => void }>((set, get) => {
  const theme = initial();
  apply(theme);
  return {
    theme,
    toggle: () => {
      const next: Theme = get().theme === 'dark' ? 'light' : 'dark';
      apply(next);
      try { localStorage.setItem(KEY, next); } catch { /* not persisted */ }
      set({ theme: next });
    },
  };
});
