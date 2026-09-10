import type { Page } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const mediaPath = (name: string) => fileURLToPath(new URL(`../fixtures/media/${name}`, import.meta.url));

export const MEDIA = {
  png: mediaPath('pixel.png'),
  wav: mediaPath('tone-1s.wav'),
  pdf: mediaPath('sample.pdf'),
  csv: mediaPath('variables.csv'),
} as const;

const MENU_LABEL = { image: 'Add image', audio: 'Add audio', pdf: 'Add pdf' } as const;

export interface AttachedFile { url: string; file_name: string }

// Drives PromptCardTopSection's attachment menu -> UploadMedia drawer for a
// single card: open the attach menu, pick the media kind, drop the file into
// the drawer's hidden input, and Save. Returns the uploaded file's server
// record (model-hub/upload-file response, first item).
export async function attachToCard(
  page: Page,
  opts: { cardIndex: number; kind: 'image' | 'audio' | 'pdf'; file: string; timeout?: number },
): Promise<AttachedFile> {
  const timeout = opts.timeout ?? 30_000;
  const card = page.locator(`[data-testid="prompt-card-${opts.cardIndex}"]`);
  await card.getByRole('button', { name: 'Attach files' }).click();
  await page.getByRole('menuitem', { name: MENU_LABEL[opts.kind] }).click();
  await page.locator('.MuiDrawer-paper input[type="file"]').setInputFiles(opts.file);

  const uploaded = page.waitForResponse(
    (r) => r.url().includes('/model-hub/upload-file/') && r.ok(),
    { timeout },
  );
  await page.locator('.MuiDrawer-paper').getByRole('button', { name: 'Save' }).click();
  const res = await uploaded;
  const body = (await res.json()) as { result: AttachedFile[] };
  return body.result[0];
}
