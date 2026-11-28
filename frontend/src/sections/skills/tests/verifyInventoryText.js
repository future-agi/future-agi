// Verify the complete UTF-8 bytes, not just the server's description of them.
export async function verifyInventoryText(text, expected) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const digest = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('');
  if (digest !== expected.replace(/^sha256:/, '')) throw new Error('The file does not match the reviewed inventory.');
  return text;
}
