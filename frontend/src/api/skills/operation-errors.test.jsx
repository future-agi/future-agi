import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { isDefiniteRejection, operationStatus } from './operation-errors';
import { useReviewedOperation } from 'src/sections/skills/review/ReleaseRequestPanel';

vi.mock('src/utils/Mixpanel', () => ({ resetUser: vi.fn() }));
import axios from 'src/utils/axios';

async function normalized(status, code) {
  const reject = axios.interceptors.response.handlers.find((handler) => handler.rejected).rejected;
  const config = { url: '/model-hub/skills/spend-policy/', method: 'put' };
  try {
    await reject({ config, response: { config, status, data: { status: false, code, message: 'Rejected', error: 'Rejected', result: null } } });
    throw new Error('Expected rejection');
  } catch (error) { return error; }
}

it.each([400, 403])('classifies the real Axios interceptor rejection for HTTP %s', async (status) => {
  const error = await normalized(status, 'spend_policy_required');
  expect(error.statusCode).toBe(status);
  expect(error.response).toBeUndefined();
  expect(operationStatus(error)).toBe(status);
  expect(isDefiniteRejection(error)).toBe(true);
});
it.each([408, 425, 429, 500, 503, undefined])('does not assert rejection for uncertain status %s', (statusCode) => {
  expect(isDefiniteRejection({ statusCode })).toBe(false);
});
it('supports raw Axios failures without confusing the semantic code with transport status', () => {
  expect(operationStatus({ response: { status: 403 }, code: 'spend_policy_required' })).toBe(403);
});
it('releases a definitively rejected reviewed operation but preserves unknown recovery', async () => {
  const rejection = await normalized(403, 'spend_policy_required');
  const send = vi.fn().mockRejectedValueOnce(rejection).mockRejectedValueOnce(new Error('lost response')).mockRejectedValueOnce(rejection);
  const { result } = renderHook(() => useReviewedOperation());
  await act(async () => { await result.current.run(send, { revision: 0 }); });
  expect(result.current.uncertain).toBe(false);
  await act(async () => { await result.current.run(send, { revision: 0 }); });
  expect(result.current.uncertain).toBe(true);
  expect(send.mock.calls[1][0].idempotencyKey).not.toBe(send.mock.calls[0][0].idempotencyKey);
  await act(async () => { await result.current.retry(); });
  expect(result.current.uncertain).toBe(true);
  expect(send.mock.calls[2][0]).toEqual(send.mock.calls[1][0]);
});

it.each([409, 422])('recognizes normalized conflict and validation status %s', (statusCode) => {
  expect(isDefiniteRejection({ statusCode })).toBe(true);
});
