export function safeArticleUrl(value?: string | null) {
  if (!value) return null;

  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null;
  } catch {
    return null;
  }
}

export function articleSourceLabel(value?: string | null) {
  if (!value?.trim()) return 'Article';
  return value.trim();
}

export function unescapeHtml(str: string | null | undefined): string {
  if (!str) return '';
  const entityMap: Record<string, string> = {
    '&amp;': '&',
    '&lt;': '<',
    '&gt;': '>',
    '&quot;': '"',
    '&#39;': "'",
    '&#x27;': "'",
    '&#x2F;': '/',
    '&#96;': '`',
    '&#x3D;': '=',
    '&ldquo;': '"',
    '&rdquo;': '"',
    '&lsquo;': "'",
    '&rsquo;': "'",
    '&ndash;': '-',
    '&mdash;': '—',
  };

  let decoded = str.replace(/&[#\w\d]+;/g, (entity) => {
    return entityMap[entity] || entity;
  });

  decoded = decoded.replace(/&#(\d+);/g, (_, dec) => {
    return String.fromCharCode(parseInt(dec, 10));
  });

  decoded = decoded.replace(/&#x([a-fA-F0-9]+);/g, (_, hex) => {
    return String.fromCharCode(parseInt(hex, 16));
  });

  return decoded;
}
