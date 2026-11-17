import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';
import { attachSeededIds } from '../_shared/contract';
import { readCatalogueSource, readPinnedPrice, assertIdentitySet } from '../_fixtures/sinks-catalogue/catalogue-source';

// Source/fixture qualification only. NOT a managed launch set, UI/API equality,
// H5 profile or A04/A05/A06/A08 product walk. Never select with GW-JOURNEY-J31.
const annotation = ['A04', 'A05', 'A06', 'A08'].map(row => ({ type: 'journey-fragment', description: JSON.stringify({
  schema: 'gateway-journey-fragment/v1', packet: 'J31', row, completeRow: false,
  oracleOnly: true, walkedStatus: 'NOT_WALKED', component: 'pinned-source-catalogue',
}) }));
test('F14 committed stock inventory and router spelling set are source-bound', {
  tag: '@journey-J31-fixture', annotation,
}, async ({}, info) => {
  const source = await readCatalogueSource();
  expect(source.sourceRef).toBe('da4c81cc623f909ce38f53e01b557ee8374d5adb');
  expect(source.stockByokIds).toEqual(['gpt-3.5-turbo', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'o1', 'o1-mini', 'turing_flash']);
  expect(source.bundledRouterIds).toEqual(['openrouter/auto', 'openrouter/auto-beta', 'openrouter/bodybuilder',
    'openrouter/free', 'openrouter/fusion', 'openrouter/pareto-code']);
  expect(source.routerSpellings).toEqual(['auto', 'auto-beta', 'bodybuilder', 'free', 'fusion',
    'openrouter/auto', 'openrouter/auto-beta', 'openrouter/bodybuilder', 'openrouter/free',
    'openrouter/fusion', 'openrouter/pareto-code', 'pareto-code']);
  expect(source.launch.state).toBe('OWED');
  const minted = `fixture-${randomUUID()}`;
  await attachSeededIds(info, 'J31', { requestIds: [minted] });
  expect(() => assertIdentitySet(source.bundledRouterIds, source.bundledRouterIds)).not.toThrow();
  for (const bad of [source.bundledRouterIds.slice(1), [...source.bundledRouterIds, minted],
    [...source.bundledRouterIds, source.bundledRouterIds[0]]]) {
    expect(() => assertIdentitySet(bad, source.bundledRouterIds)).toThrow('CATALOGUE_EXACT_IDS');
  }
  expect(() => assertIdentitySet(source.bundledRouterIds, source.bundledRouterIds)).not.toThrow();
  await info.attach('catalogue-source-counts', { contentType: 'application/json', body: JSON.stringify({
    stockByok: source.stockByokIds.length, bundled: source.bundledModelIds.length,
    routers: source.bundledRouterIds.length, launch: 'OWED', completeRow: false,
  }) });
});

test('F14 pinned tilde-prefixed model identities survive complete source validation', {
  tag: '@journey-J31-fixture', annotation,
}, async ({}, info) => {
  // Exact tilde-prefixed subset of openrouter_snapshot.json at da4c81cc62.
  // This is source syntax/inventory qualification, NOT a managed launch set.
  const expected = ['~anthropic/claude-fable-latest', '~anthropic/claude-haiku-latest',
    '~anthropic/claude-opus-latest', '~anthropic/claude-sonnet-latest',
    '~deepseek/deepseek-flash-latest', '~deepseek/deepseek-pro-latest', '~deepseek/deepseek-v4-flash-latest',
    '~google/gemini-flash-latest', '~google/gemini-pro-latest', '~moonshotai/kimi-latest',
    '~openai/gpt-astra-latest', '~openai/gpt-luna-latest', '~openai/gpt-mini-latest',
    '~openai/gpt-sol-latest', '~openai/gpt-terra-latest', '~unbiased/pareto-latest',
    '~x-ai/grok-latest', '~z-ai/glm-flash-latest', '~z-ai/glm-latest'];
  const source = await readCatalogueSource();
  expect(source.bundledModelIds).toHaveLength(463);
  const actual = source.bundledModelIds.filter(model => model.startsWith('~'));
  expect(actual).toEqual(expected);
  expect(() => assertIdentitySet(actual, expected)).not.toThrow();
  const price = { sourceRef: source.sourceRef, modelId: '~unbiased/pareto-latest',
    dimension: 'prompt', unit: 'USD/input-token', decimalUsd: '0.0000025', perMillionUsd: '2.5' };
  expect(await readPinnedPrice(price.modelId, 'prompt')).toEqual(price);
  const minted = `fixture-${randomUUID()}`;
  await attachSeededIds(info, 'J31', { requestIds: [minted] });
  for (const bad of [actual.slice(1), [...actual, `~${minted}`], [...actual, actual[0]]]) {
    expect(() => assertIdentitySet(bad, expected)).toThrow('CATALOGUE_EXACT_IDS');
  }
  for (const invalid of ['~', `~~${minted}`, `${minted}~tail`, `~${minted}?query`,
    `~${minted}\n`, `~${'a'.repeat(255)}`]) {
    await expect(readPinnedPrice(invalid, 'prompt')).rejects.toThrow('CATALOGUE_SOURCE_ID');
  }
  const restored = await readCatalogueSource();
  expect(restored.bundledModelIds).toEqual(source.bundledModelIds);
  expect(restored.bundledModelIds.filter(model => model.startsWith('~'))).toEqual(expected);
  expect(await readPinnedPrice(price.modelId, 'prompt')).toEqual(price);
});

test('F14 independent decimal price units refuse output-image and generation inference', {
  tag: '@journey-J31-fixture', annotation,
}, async () => {
  // Unit contract pinned to endpoint.go:Prices and openrouter.go:pricingKeys.
  // A request charge is NOT a per-generation quote; image is an INPUT image.
  const token = await readPinnedPrice('meta/muse-spark-open', 'prompt');
  expect(token).toMatchObject({ decimalUsd: '0.00000125', unit: 'USD/input-token' });
  expect(token.perMillionUsd).toBe('1.25');
  await expect(readPinnedPrice('meta/muse-spark-open', 'output_image')).rejects.toThrow('CATALOGUE_PRICE_UNIT_OWED');
  await expect(readPinnedPrice('meta/muse-spark-open', 'generation')).rejects.toThrow('CATALOGUE_PRICE_UNIT_OWED');
  await expect(readPinnedPrice('openrouter/auto', 'prompt')).rejects.toThrow('CATALOGUE_DYNAMIC_PRICE');
  expect((await readPinnedPrice('meta/muse-spark-open', 'prompt')).perMillionUsd).toBe('1.25');
});
