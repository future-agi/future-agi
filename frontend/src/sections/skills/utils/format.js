export function formatBytes(bytes = 0) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Number((bytes / 1024).toFixed(1))} KiB`;
  return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
}

export function formatTokens(tokens = 0) {
  return `≈ ${Number(tokens).toLocaleString()} tokens`;
}

export function shortDigest(digest = '') {
  return digest.replace(/^sha256:/, '').slice(0, 12);
}
