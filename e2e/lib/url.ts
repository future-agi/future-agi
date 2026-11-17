/** Matches a URL that ends exactly at `path` (regex metacharacters escaped). */
export function exactPath(path: string) {
  return new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`);
}
