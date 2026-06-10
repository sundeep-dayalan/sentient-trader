import { useEffect, useState } from 'react';
import { applyTheme, getStoredTheme, persistTheme, THEME_STORAGE_KEY, type ThemeMode } from '@/lib/theme';
import { AnimatedThemeToggler } from '@/components/ui/animated-theme-toggler';

export default function ThemeToggle({ variant = 'icon' }: { variant?: 'icon' | 'menu' }) {
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

  // Controlled toggle: AnimatedThemeToggler runs the View Transition reveal and
  // calls this synchronously inside the transition, so persistTheme's DOM mutation
  // is captured in the snapshot. persistTheme also fires st-theme-change so the
  // other toggle instance stays in sync.
  function handleThemeChange(next: ThemeMode) {
    setTheme(next);
    persistTheme(next);
  }

  const isDark = theme === 'dark';
  // When dark, the action switches to light (show a sun); when light, to dark (moon).
  const icon = isDark ? (
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
  );

  if (variant === 'menu') {
    return (
      <AnimatedThemeToggler
        theme={theme}
        onThemeChange={handleThemeChange}
        className="flex w-full items-center justify-between gap-2.5 rounded-lg px-3 py-2 text-left text-xs font-medium text-secondary transition hover:bg-hover hover:text-primary"
      >
        <span className="flex items-center gap-2.5">
          <span className="flex h-3.5 w-3.5 items-center justify-center text-muted">{icon}</span>
          {isDark ? 'Light mode' : 'Dark mode'}
        </span>
        <span className="rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-muted">
          {isDark ? 'Dark' : 'Light'}
        </span>
      </AnimatedThemeToggler>
    );
  }

  return (
    <AnimatedThemeToggler
      theme={theme}
      onThemeChange={handleThemeChange}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      className="flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-surface text-muted transition-all duration-200 hover:border-accent-border hover:text-accent"
    >
      {icon}
    </AnimatedThemeToggler>
  );
}
