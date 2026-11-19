import type { Locator } from '@playwright/test';
import { strToU8, zipSync } from 'fflate';

/** Synthetic in-memory ZIPs only; product parsing happens through the real upload input.
 * ImportPreviewRequest in model_hub/serializers/skills_hub.py receives multipart `files`.
 * Stored ZIP members keep long repeated UTF-8 fixtures below the parser's ratio limit.
 */
export function skillsArchive(entries: Record<string, string>): Buffer {
  return Buffer.from(zipSync(Object.fromEntries(Object.entries(entries).map(
    ([path, text]) => [path, strToU8(text)],
  )), { level: 0, mtime: new Date('2000-01-01T00:00:00Z') }));
}

export async function uploadSkillsArchive(input: Locator, entries: Record<string, string>) {
  const buffer = skillsArchive(entries);
  await input.setInputFiles({ name: 'synthetic-skills.zip', mimeType: 'application/zip', buffer },
    { timeout: 60_000 });
  return buffer;
}
