import { expect, it } from 'vitest';
import { contextBindingInput, mapContextBinding } from './context-consumers';
import wire from './__tests__/r3_l07/wire-fixtures.json';

it('keeps a pinned native manifest and server digest on reads, but never asserts a digest on writes', () => {
  const configuration = wire.NativeConfiguration;
  const mapped = mapContextBinding({ ...wire.BindingOut, configuration, configuration_digest: 'sha256:server-derived' });
  expect(mapped.configuration).toBe(configuration);
  expect(mapped.configurationDigest).toBe('sha256:server-derived');
  const body = contextBindingInput(mapped);
  expect(body.configuration).toBe(configuration);
  expect(body).not.toHaveProperty('configuration_digest');
  expect(body).not.toHaveProperty('configurationDigest');
});

it('distinguishes legacy omission from explicit configuration removal', () => {
  const legacyWire = { ...wire.BindingOut };
  delete legacyWire.configuration;
  delete legacyWire.configuration_digest;
  const legacy = mapContextBinding(legacyWire);
  expect(contextBindingInput(legacy)).not.toHaveProperty('configuration');
  const cleared = mapContextBinding({ ...wire.BindingOut, configuration: null, configuration_digest: '' });
  expect(contextBindingInput(cleared)).toHaveProperty('configuration', null);
  expect(cleared.configurationDigest).toBe('');
});
