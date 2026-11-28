import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { publicApiOrigin } from './origin';

const config = vi.hoisted(() => ({ host: 'https://fallback.example.invalid' }));
vi.mock('src/config-global', () => ({ get HOST_API() { return config.host; } }));
function frontend(hostname) {
  window.location = { hostname, origin: `https://${hostname}` };
}
beforeEach(() => {
  config.host = 'https://fallback.example.invalid';
  vi.stubGlobal('window', { __FUTURE_AGI_CONFIG__: {} });
  vi.stubEnv('VITE_PUBLIC_API_ORIGIN', '');
  frontend('app.example.invalid');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('publicApiOrigin', () => {
  it('uses runtime configuration ahead of build-time configuration', () => {
    window.__FUTURE_AGI_CONFIG__.VITE_PUBLIC_API_ORIGIN = 'https://runtime.example.invalid/';
    vi.stubEnv('VITE_PUBLIC_API_ORIGIN', 'https://build.example.invalid');
    expect(publicApiOrigin()).toEqual({ origin: 'https://runtime.example.invalid', error: null });
  });
  it('uses build configuration, then HOST_API, but never the frontend origin', () => {
    vi.stubEnv('VITE_PUBLIC_API_ORIGIN', 'https://build.example.invalid');
    expect(publicApiOrigin().origin).toBe('https://build.example.invalid');
    vi.stubEnv('VITE_PUBLIC_API_ORIGIN', '');
    expect(publicApiOrigin().origin).toBe('https://fallback.example.invalid');
  });
  it.each(['localhost', '127.0.0.1'])('allows local HTTP only for a loopback frontend (%s)', (hostname) => {
    frontend(hostname);
    config.host = 'http://localhost:8000';
    expect(publicApiOrigin()).toEqual({ origin: 'http://localhost:8000', error: null });
    config.host = 'http://127.0.0.1:8000';
    expect(publicApiOrigin().error).toBeNull();
    frontend('app.example.invalid');
    expect(publicApiOrigin()).toEqual({ origin: null, error: 'not_https' });
  });
  it.each(['http://api.example.invalid', 'ftp://localhost', 'invalid', '/api', 'https://user:password@example.invalid', 'https://example.invalid/?secret=1', 'https://example.invalid/#fragment'])('rejects unsafe configuration without falling back (%s)', (origin) => {
    frontend('localhost');
    window.__FUTURE_AGI_CONFIG__.VITE_PUBLIC_API_ORIGIN = origin;
    expect(publicApiOrigin()).toEqual({ origin: null, error: 'not_https' });
  });
  it('does not confuse a localhost-looking remote host with loopback', () => {
    frontend('localhost.example.invalid');
    config.host = 'http://localhost:8000';
    expect(publicApiOrigin().error).toBe('not_https');
  });
});
