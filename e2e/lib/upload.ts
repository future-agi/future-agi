import type { Locator } from '@playwright/test';
import { fileURLToPath } from 'node:url';

/** Supply a checked-in synthetic fixture to the product's real file input. */
export async function uploadFixture(input: Locator, fixture: 'catalog-regions.csv') {
  await input.setInputFiles(fileURLToPath(new URL(`../fixtures/${fixture}`, import.meta.url)),
    { timeout: 60_000 });
}
