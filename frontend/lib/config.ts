// Base path prefix for internal API calls.
// Empty locally (Next.js dev server serves at root).
// Set to /sentient-trader in Netlify env so the portfolio proxy can route it.
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
