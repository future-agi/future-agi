import { test, expect } from '../lib/fixtures';
import { uploadKnowledgeDocument } from '../lib/kb-upload';

test('KB text fixture uploads a file through the public API', async ({ actor, request }, testInfo) => {
  const name = `e2e-kb-fixture-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const seeded = await uploadKnowledgeDocument(request, actor, name, `Synthetic document ${name}\n`);
  await testInfo.attach('seeded-kb', { contentType: 'application/json', body: JSON.stringify(seeded) });
  const files = await actor.api.post<{ result: { table_data: { id: string; name: string }[] } }>(
    '/model-hub/knowledge-base/files/', { kb_id: seeded.kbId, page_number: 0, page_size: 25 });
  expect(files.result.table_data.map(({ id, name }) => ({ id, name })))
    .toEqual([{ id: seeded.fileId, name: seeded.filename }]);
});
