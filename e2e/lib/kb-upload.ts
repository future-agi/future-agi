import type { APIRequestContext } from '@playwright/test';
import { expect } from '@playwright/test';
import { E2E } from './env';
import type { TestActor } from './provisioning';

export type KnowledgeActor = Pick<TestActor, 'tokens' | 'organizationId' | 'workspaceId'>;

export function knowledgeHeaders(actor: KnowledgeActor): Record<string, string> {
  return { Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
}

/** Synthetic text upload through CreateKnowledgeBaseView.post. No indexing stub. */
export async function uploadKnowledgeDocument(
  req: APIRequestContext, actor: KnowledgeActor, name: string, contents: string,
): Promise<{ kbId: string; fileId: string; filename: string }> {
  if (new URL(E2E.apiUrl).hostname !== 'localhost') throw new Error('KB fixture requires localhost');
  const filename = `${name}.txt`;
  // LegacyKnowledgeBaseMutationRequestSerializer plus request.FILES.getlist('file').
  const response = await req.post(`${E2E.apiUrl}/model-hub/knowledge-base/`, {
    headers: knowledgeHeaders(actor),
    multipart: { name, file: { name: filename, mimeType: 'text/plain', buffer: Buffer.from(contents) } },
  });
  expect(response.status(), await response.text()).toBe(200);
  const body = await response.json();
  // LegacyKnowledgeBaseCreateResultSerializer is the response contract.
  expect(body.result.kb_name).toBe(name);
  expect(body.result.file_ids).toHaveLength(1);
  return { kbId: body.result.kb_id, fileId: body.result.file_ids[0], filename };
}
