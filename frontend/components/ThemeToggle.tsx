import { useEffect, useState } from 'react';
import { applyTheme, getStoredTheme, persistTheme, THEME_STORAGE_KEY, type ThemeMode } from '@/lib/theme';

export default function ThemeToggle() {
  const [theme, setTheme] = useState<ThemeMode>(getStoredTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const syncTheme = () => setTheme(getStoredTheme());
    const syncThemeFromEvent = (event: Event) => {
      const next = (event as CustomEvent<ThemeMode>).detail;
      setTheme(next === 'dark' ? 'dark' : 'light');
    };
    const syncThemeFromStorage = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY) syncTheme();
    };

    window.addEventListener('storage', syncThemeFromStorage);
    window.addEventListener('st-theme-change', syncThemeFromEvent);

    return () => {
      window.removeEventListener('storage', syncThemeFromStorage);
      window.removeEventListener('st-theme-change', syncThemeFromEvent);
    };
  }, []);

  function toggle() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    persistTheme(next);
  }

  return (
    <button
      onClick={toggle}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-surface text-muted transition-all duration-200 hover:border-accent-border hover:text-accent"
    >
      {theme === 'dark' ? (
        /* sun */
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="5" />
          <line x1="12" y1="1" x2="12" y2="3" />
          <line x1="12" y1="21" x2="12" y2="23" />
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
          <line x1="1" y1="12" x2="3" y2="12" />
          <line x1="21" y1="12" x2="23" y2="12" />
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
        </svg>
      ) : (
        /* moon */
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
    </button>
  );
}
