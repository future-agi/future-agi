import { test, expect } from '../../lib/scope-actors';

// model_hub/urls.py and ManualDatasetCreateRequestSerializer.
const CREATE = '/model-hub/develops/create-dataset-manually/';
const ZERO_ROWS_REFUSAL = 'Ensure this value is greater than or equal to 1. '
  + 'To create an empty dataset with no rows, send "number_of_rows": 0 with a '
  + '"columns" list that names each column, for example [{"name": "input", "type": "text"}].';
const columns = [
  { name: 'question', type: 'text', role: 'input' },
  { name: 'expected_count', type: 'integer', role: 'expected' },
  { name: 'context', type: 'json', role: 'metadata' },
];

test('manual creation persists ordered named types and roles instead of generic columns', async ({ scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(180_000);
  const actor = scopeActors.ownerA;
  const name = `e2e-blank-schema-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const created = await scopeActors.send<{ result: { dataset_id: string } }>(actor, 'POST', CREATE,
    { dataset_name: name, number_of_rows: 2, number_of_columns: 3, columns });
  expect(created.status).toBe(200);
  const id = created.body.result.dataset_id;
  await testInfo.attach('created-dataset', { contentType: 'application/json', body: JSON.stringify({ id, name }) });
  const stored = await scopeProbe.pg<{ name: string; type: string; role: string }>(
    `SELECT c.name, c.data_type AS type, d.column_config->c.id::text->>'role' AS role
     FROM model_hub_dataset d JOIN model_hub_column c ON c.dataset_id = d.id
     WHERE d.id = $1 AND NOT c.deleted ORDER BY array_position(d.column_order, c.id::text)`, [id]);
  expect(stored).toEqual(columns);
});

test('manual creation supports zero rows and derives column count', async ({ scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(180_000);
  const name = `e2e-blank-zero-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const created = await scopeActors.send<{ result: { dataset_id: string; rows_created: number; columns_created: number } }>(
    scopeActors.ownerA, 'POST', CREATE, { dataset_name: name, number_of_rows: 0, columns });
  expect(created.status).toBe(200);
  expect(created.body.result).toMatchObject({ rows_created: 0, columns_created: 3 });
  const id = created.body.result.dataset_id;
  expect(await scopeProbe.pg('SELECT id FROM model_hub_row WHERE dataset_id = $1', [id])).toEqual([]);
  expect(await scopeProbe.pg('SELECT id FROM model_hub_cell WHERE dataset_id = $1', [id])).toEqual([]);
});

test('invalid manual schemas are rejected atomically', async ({ scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(180_000);
  const actor = scopeActors.ownerA;
  const prefix = `e2e-blank-invalid-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const cases = [
    { columns: [], number_of_rows: 0 },
    { columns: [{ name: '  ', type: 'text', role: 'input' }] },
    { columns: [columns[0], { ...columns[1], name: ' QUESTION ' }] },
    { columns: [{ ...columns[0], type: 'unsupported' }] },
    { columns: [{ ...columns[0], role: 'split' }] },
    { columns: [{ ...columns[0], extra: 'must-not-be-silently-dropped' }] },
    { columns, number_of_columns: 2 },
    { columns, number_of_rows: 101 },
    { columns, number_of_rows: -1 },
  ];
  for (const [index, body] of cases.entries()) {
    const name = `${prefix}-${index}`;
    const result = await scopeActors.send<{ details: { number_of_rows?: string[] } }>(actor, 'POST', CREATE,
      { dataset_name: name, number_of_rows: 1, ...body });
    expect(result.status, JSON.stringify(body)).toBe(400);
    expect(await scopeProbe.pg('SELECT id FROM model_hub_dataset WHERE name = $1 AND organization_id = $2',
      [name, actor.organizationId])).toEqual([]);
    if (body.number_of_rows === 0) {
      expect(result.body.details).toEqual({ number_of_rows: [ZERO_ROWS_REFUSAL] });
    }
  }
});

for (const { label, body, expectedColumns } of [
  { label: 'omitted columns with explicit counts', body: { number_of_rows: 1, number_of_columns: 2 }, expectedColumns: 2 },
  { label: 'empty columns with one row', body: { columns: [], number_of_rows: 1 }, expectedColumns: 1 },
  { label: 'empty columns with default counts', body: { columns: [] }, expectedColumns: 1 },
]) {
  test(`count-only clients still work: ${label}`, async ({ scopeActors, scopeProbe }, testInfo) => {
    test.setTimeout(180_000);
    const prefix = `e2e-blank-legacy-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const legacy = await scopeActors.send<{ result: { dataset_id: string; rows_created: number; columns_created: number } }>(
      scopeActors.ownerA, 'POST', CREATE, { dataset_name: prefix, ...body });
    expect(legacy.status).toBe(200);
    expect(legacy.body.result).toMatchObject({ rows_created: 1, columns_created: expectedColumns });
    const id = legacy.body.result.dataset_id;
    expect(await scopeProbe.pg('SELECT name, data_type FROM model_hub_column WHERE dataset_id = $1 ORDER BY name', [id]))
      .toEqual(Array.from({ length: expectedColumns }, (_, index) => ({ name: `Column ${index + 1}`, data_type: 'text' })));
    expect(await scopeProbe.pg('SELECT id FROM model_hub_row WHERE dataset_id = $1', [id])).toHaveLength(1);
    expect(await scopeProbe.pg('SELECT id FROM model_hub_cell WHERE dataset_id = $1', [id])).toHaveLength(expectedColumns);
  });
}
