const sha = async (text) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))), (byte) => byte.toString(16).padStart(2, '0')).join('');

/** Match the server's canonical manifest, including Python Unicode ordering. */
export async function skillManifestDigest(files) {
  const manifest = files.map(({ path, sha256 }) => {
    if (typeof path !== 'string' || !/^[a-f0-9]{64}$/.test(sha256)) throw new Error('The file manifest is incomplete.');
    return [path, sha256];
  });
  manifest.sort(([left], [right]) => {
    const a = Array.from(left, (char) => char.codePointAt(0));
    const b = Array.from(right, (char) => char.codePointAt(0));
    for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
      if (a[index] !== b[index]) return a[index] - b[index];
    }
    return a.length - b.length;
  });
  const canonical = JSON.stringify(manifest).replace(/[\u007f-￿]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return `sha256:${await sha(canonical)}`;
}

export async function skillFilesDigest(files) {
  return skillManifestDigest(await Promise.all(files.map(async ({ path, content }) => ({ path, sha256: await sha(content) }))));
}
