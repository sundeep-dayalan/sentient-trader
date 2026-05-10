export function safeArticleUrl(value?: string | null) {
  if (!value) return null;

  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export function articleSourceLabel(value?: string | null) {
  if (!value?.trim()) return "Article";
  return value.trim();
}
