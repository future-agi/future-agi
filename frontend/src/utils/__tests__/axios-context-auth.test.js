import { AxiosHeaders } from 'axios';
import { beforeEach, expect, it, vi } from 'vitest';

const auth = vi.hoisted(() => ({ refresh: vi.fn(), clear: vi.fn(), session: vi.fn(), queue: vi.fn() }));
vi.mock('../Mixpanel', () => ({ resetUser: vi.fn() }));
vi.mock('src/auth/context/jwt/utils', () => ({
  addToQueue: auth.queue, clearTokens: auth.clear, getIsRefreshing: () => false,
  getRefreshToken: () => 'synthetic-human-refresh', getRememberMe: () => false,
  processQueue: vi.fn(), refreshTokenRequest: auth.refresh, setIsRefreshing: vi.fn(), setSession: auth.session,
}));

import axiosInstance from '../axios';

beforeEach(() => { vi.clearAllMocks(); });

it.each(['X-Api-Key', 'x-secret-key'])('rejects ambiguous %s before sending', (name) => {
  const fulfilled = axiosInstance.interceptors.request.handlers.find((handler) => handler.fulfilled).fulfilled;
  expect(() => fulfilled({ url: '/model-hub/skills/consumption/resolve/', method: 'get', headers: { authorization: 'Context synthetic-test-token', [name]: 'synthetic-broad-key' } })).toThrow('cannot be combined');
});

it.each([401, 403, 503])('never refreshes or retries Context auth after %s', async (status) => {
  const rejected = axiosInstance.interceptors.response.handlers.find((handler) => handler.rejected).rejected;
  const config = { url: '/model-hub/skills/consumption/resolve/', method: 'get', headers: new AxiosHeaders({ authorization: 'Context synthetic-test-token' }) };
  const error = { config, response: { status, config, data: { status: false, code: 'context_credential_expired', message: 'Expired', error: 'Expired', result: null } } };
  await expect(rejected(error)).rejects.toBe(error);
  expect(auth.refresh).not.toHaveBeenCalled();
  expect(auth.clear).not.toHaveBeenCalled();
  expect(auth.session).not.toHaveBeenCalled();
  expect(auth.queue).not.toHaveBeenCalled();
  expect(config.headers.get('Authorization')).toBe('Context synthetic-test-token');
});
