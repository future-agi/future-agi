import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import CheckTracing from './CheckTracing';
import { NOW, failure, renderRuntime, success } from './testFixtures';

const hooks = vi.hoisted(() => ({ latest: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({ ...(await importOriginal()), useLatestLoad: hooks.latest }));

const idle = () => ({ data: undefined, isPending: true, isError: false, fetchStatus: 'idle', refetch: vi.fn() });
const MINUTE = 60 * 1000;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(NOW);
  hooks.latest.mockReturnValue(idle());
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('CheckTracing', () => {
  it('waits for the next load only after the button is pressed', () => {
    const view = renderRuntime(<CheckTracing id="skill-a" name="refund-policy" now={NOW} />);
    expect(hooks.latest).toHaveBeenLastCalledWith({ kind: 'skill', id: 'skill-a', since: null, active: false });
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(hooks.latest).toHaveBeenLastCalledWith({ kind: 'skill', id: 'skill-a', since: new Date(NOW).toISOString(), active: true });
    view.update(<CheckTracing id="skill-a" name="refund-policy" now={NOW + 12 * 1000} />);
    expect(screen.getByText('Waiting for the next load… 0:12')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop waiting' })).toBeInTheDocument();
  });

  it('reports the load that arrived', () => {
    const view = renderRuntime(<CheckTracing id="skill-a" name="refund-policy" now={NOW} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    hooks.latest.mockReturnValue(success({ load: { version: 2, agent: { name: 'refund-agent' }, project: { name: 'p' }, receivedAt: new Date(NOW).toISOString() }, fetch: null }));
    view.update(<CheckTracing id="skill-a" name="refund-policy" now={NOW + 4000} />);
    expect(screen.getByText('Received refund-policy v2 from refund-agent 4 seconds ago')).toBeInTheDocument();
  });

  it("gives the last fetch's reason when its SDK can't record loads", () => {
    renderRuntime(<CheckTracing id="skill-a" name="refund-policy" now={NOW} />);
    hooks.latest.mockReturnValue(success({ load: null, fetch: { sdkVersion: '0.9.2', client: 'futureagi-python', tracingMode: 'on', canRecord: false, minSdkVersion: '1.4.0' } }));
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(screen.getByText("The last fetch came from futureagi-python 0.9.2, which can't record loads. Upgrade to 1.4.0 or later.")).toBeInTheDocument();
  });

  it('stops after 10 minutes and says no load arrived', () => {
    const view = renderRuntime(<CheckTracing id="skill-a" name="refund-policy" now={NOW} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    hooks.latest.mockReturnValue(success({ load: null, fetch: { canRecord: true } }));
    view.update(<CheckTracing id="skill-a" name="refund-policy" now={NOW + 10 * MINUTE} />);
    expect(screen.getByText('No load arrived in 10 minutes')).toBeInTheDocument();
    expect(screen.getByText('Consumers can record loads, but none arrived in this window.')).toBeInTheDocument();
    expect(hooks.latest.mock.calls.at(-1)[0].active).toBe(false);
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument();
  });

  it('shows an inline error with Retry', () => {
    renderRuntime(<CheckTracing id="skill-a" name="refund-policy" now={NOW} />);
    hooks.latest.mockReturnValue(failure('runtime_read_unavailable'));
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Runtime data is temporarily unavailable.');
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
  });

  it('is disabled with the reason for viewers', () => {
    renderRuntime(<CheckTracing id="skill-a" name="refund-policy" canCheck={false} />);
    expect(screen.getByRole('button', { name: 'Check tracing' })).toBeDisabled();
    expect(screen.getByText('Only people who can install can check tracing.')).toBeInTheDocument();
  });

  it('reads context.load for an agent context', () => {
    renderRuntime(<CheckTracing kind="agent_context" id="ctx-a" name="support-agent" now={NOW} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(hooks.latest).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'agent_context', id: 'ctx-a', active: true }));
  });
});
