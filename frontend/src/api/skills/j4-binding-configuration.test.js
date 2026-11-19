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
  // The current BindingOut always carries configuration (null when none); a legacy
  // payload is one from before J4, without either configuration field.
  const { configuration: _configuration, configuration_digest: _digest, ...legacyOut } = wire.BindingOut;
  const legacy = mapContextBinding(legacyOut);
  expect(legacy).not.toHaveProperty('configurationDigest');
  expect(contextBindingInput(legacy)).not.toHaveProperty('configuration');
  // Reading a current binding without configuration and writing it back keeps
  // the server's null; nothing is invented.
  expect(contextBindingInput(mapContextBinding(wire.BindingOut))).toHaveProperty('configuration', null);
  const cleared = mapContextBinding({ ...wire.BindingOut, configuration: null, configuration_digest: '' });
  expect(contextBindingInput(cleared)).toHaveProperty('configuration', null);
  expect(cleared.configurationDigest).toBe('');
});
