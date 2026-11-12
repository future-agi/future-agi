import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const swagger = JSON.parse(readFileSync(path.join(root, 'api_contracts/openapi/swagger.json'), 'utf8'));
const generated = readFileSync(path.join(root, 'frontend/src/generated/api-contracts/api.schemas.ts'), 'utf8');

function declaration(name) {
  const start = generated.indexOf(`export interface ${name} {`);
  assert.notEqual(start, -1, `${name} must be generated`);
  const end = generated.indexOf('\nexport ', start + 1);
  return generated.slice(start, end < 0 ? undefined : end);
}

test('SQL nullable wire fields retain null in the generated client', () => {
  for (const [schema, fields] of Object.entries({
    SQLExecution: { finished_at: 'string' },
    SQLResult: { next_offset: 'number' },
    SQLEstimateSource: { parts: 'number', marks: 'number', max_rows: 'number' },
    SQLEstimate: { exceeds_scan_bound: 'boolean' },
  })) {
    const definition = swagger.definitions[schema];
    const block = declaration(`${schema}Api`);
    for (const [field, type] of Object.entries(fields)) {
      assert.equal(definition.properties[field]['x-nullable'], true, `${schema}.${field} wire contract`);
      const optional = definition.required?.includes(field) ? '' : '?';
      assert.ok(block.includes(`${field}${optional}: ${type} | null;`), `${schema}.${field} client contract`);
    }
  }
});

test('SQL row cells include JSON primitives, arrays, objects and null', () => {
  assert.match(generated, /export type SQLResultApiRowsItemItem = JsonValueApi;/);
  for (const member of ['string', 'number', 'boolean', 'null', 'JsonValueApi[]', '{ [key: string]: JsonValueApi }']) {
    assert.ok(generated.includes(member), `recursive JSON member ${member}`);
  }
});
