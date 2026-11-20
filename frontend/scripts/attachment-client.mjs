// Orval's fetch template applies JSON serialization to an octet-stream Blob
// and omits manual request headers. Limit the correction to these operations;
// fail generation if the template changes instead of silently dropping bytes.
export function attachmentTransport(source) {
  function update(name, changes) {
    const start = source.indexOf(`export const ${name} =`);
    if (start < 0) throw new Error(`Missing generated operation ${name}`);
    const next = source.indexOf("\nexport ", start + 1);
    const end = next < 0 ? source.length : next;
    let block = source.slice(start, end);
    for (const [pattern, replacement] of changes) {
      if (!pattern.test(block)) throw new Error(`Unexpected ${name} template`);
      block = block.replace(pattern, replacement);
    }
    source = source.slice(0, start) + block + source.slice(end);
  }
  update("streamAttachmentContent", [
    [
      /JSON\.stringify\(streamAttachmentContentBody\)/,
      "streamAttachmentContentBody",
    ],
  ]);
  update("bootstrapAttachmentSession", [
    [
      /options\?: RequestInit/,
      "idempotencyKey: string,\n  options?: RequestInit",
    ],
    [
      /return apiMutator/,
      `if (typeof idempotencyKey !== "string" || !idempotencyKey.trim()) {
    throw new TypeError("Idempotency-Key is required");
  }
  const headers = new Headers(options?.headers);
  headers.set("Content-Type", "application/json");
  headers.set("Idempotency-Key", idempotencyKey);
  return apiMutator`,
    ],
    [
      /headers:\s*\{\s*["']Content-Type["']:\s*["']application\/json["'],\s*\.\.\.options\?\.headers,?\s*\}/,
      "headers",
    ],
  ]);
  return source;
}
