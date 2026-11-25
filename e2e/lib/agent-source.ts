import type { APIRequestContext } from '@playwright/test';
import { expect } from '@playwright/test';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { E2E } from './env';
import { knowledgeHeaders, type KnowledgeActor } from './kb-upload';

export const AGENT_SOURCE_DIR = fileURLToPath(new URL('../fixtures/agent-source', import.meta.url));
export const AGENT_SOURCE_NAME = 'agent-source';

export interface UploadedSource { source_id: string; name: string; file_count: number; total_bytes: number }

export async function uploadAgentSource(req: APIRequestContext, actor: KnowledgeActor): Promise<UploadedSource> {
  if (new URL(E2E.apiUrl).hostname !== 'localhost') throw new Error('agent source fixture requires localhost');
  const form = new FormData();
  for (const file of readdirSync(AGENT_SOURCE_DIR).sort()) {
    form.append('files', new Blob([readFileSync(path.join(AGENT_SOURCE_DIR, file))]), file);
    form.append('paths', file);
  }
  form.append('name', AGENT_SOURCE_NAME);
  const response = await req.post(`${E2E.apiUrl}/simulate/api/harness-jobs/sources/`, {
    headers: knowledgeHeaders(actor), multipart: form,
  });
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as UploadedSource;
}
