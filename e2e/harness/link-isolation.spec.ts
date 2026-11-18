import { test, expect } from '@playwright/test';
import { isLocalBrowserUrl } from '../lib/local-only-browser';
import { inspectManagedMock, validateMockNetworks, validateFixtureNetwork, type Container } from '../lib/managed-mock';

const project = 'fixture-project';
const network = { Driver: 'bridge', Internal: true, Labels: {
  'com.docker.compose.project': project, 'com.docker.compose.network': 'link-public',
} };
const container = { HostConfig: { Dns: [], DnsSearch: [] }, NetworkSettings: { Networks: {
  default: { Aliases: ['app'] }, 'link-public': { Aliases: ['app'] },
} } } as unknown as Container;

test('link fixture attestation accepts the exact internal network only', () => {
  validateMockNetworks('app', container, ['default', 'link-public']);
  validateFixtureNetwork(network, project, 'link-public');
  for (const expected of [['default'], ['default', 'arbitrary'], ['default', 'link-public', 'other']]) {
    expect(() => validateMockNetworks('app', container, expected)).toThrow('network or DNS override');
  }
  for (const field of ['Dns', 'DnsSearch'] as const) {
    const modified = structuredClone(container); modified.HostConfig[field] = ['external.invalid'];
    expect(() => validateMockNetworks('app', modified, ['default', 'link-public'])).toThrow('network or DNS override');
  }
  const modified = structuredClone(container); modified.NetworkSettings.Networks['link-public'].Aliases = ['other'];
  expect(() => validateMockNetworks('app', modified, ['default', 'link-public'])).toThrow('network or DNS override');
  for (const changed of [{ ...network, Internal: false }, { ...network, Driver: 'host' },
    { ...network, Labels: { ...network.Labels, 'com.docker.compose.project': 'other' } },
    { ...network, Labels: { ...network.Labels, 'com.docker.compose.network': 'other' } }]) {
    expect(() => validateFixtureNetwork(changed, project, 'link-public')).toThrow('unmanaged fixture network');
  }
  expect(() => validateFixtureNetwork(network, project, 'arbitrary')).toThrow('unmanaged fixture network');
});

test('link acceptance browser refuses nonlocal hosts and malformed URLs', () => {
  for (const url of ['http://localhost:3100/', 'http://127.0.0.1:8100/', 'http://[::1]:8100/']) {
    expect(isLocalBrowserUrl(url)).toBe(true);
  }
  for (const url of ['https://external.invalid/', 'https://localhost.external.invalid/',
    'http://localhost@external.invalid/', 'file:///tmp/example', 'data:text/plain,example', 'invalid']) {
    expect(isLocalBrowserUrl(url)).toBe(false);
  }
});

test('managed stack attests the isolated link network before mock registration', async ({}, testInfo) => {
  const receipt = inspectManagedMock();
  await testInfo.attach('managed-mock-receipt', { contentType: 'application/json', body: JSON.stringify(receipt) });
});


test('local editor bootstrap is served as JavaScript with pinned assets', async ({ request }) => {
  const response = await request.get('/e2e-monaco/bootstrap.js');
  expect(response.status()).toBe(200);
  expect(response.headers()['content-type']).toMatch(/^(application|text)\/javascript/);
  expect(await response.text()).toContain("window.require(['vs/editor/editor.main']");
  const loader = await request.get('/e2e-monaco/vs/loader.js');
  expect(loader.status()).toBe(200);
  expect(loader.headers()['content-type']).toMatch(/^(application|text)\/javascript/);
});
