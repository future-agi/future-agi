import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen, within } from '@testing-library/react';
import AgentPanel, { agentPanelInput } from './AgentPanel';
import { setSkillTracesOverride } from './skillTracesFlag';
import { ROWS, V4, agent, failure, links, pending, renderRuntime, success } from './__tests__/fixtures';

const hooks = vi.hoisted(() => ({ agent: vi.fn() }));
vi.mock('src/api/skills/runtime', async (importOriginal) => ({ ...(await importOriginal()), useRuntimeAgent: hooks.agent }));
vi.mock('src/components/iconify', () => ({ default: () => null }));

const refund = agentPanelInput({ ...agent('refund-agent', 'p1', 'acme-support-production'), context: { id: 'ctx-1', name: 'support-agent', version: 2 }, bindingId: 'binding-1' });
const rows = [{ ...ROWS[1], skillId: 'skill-a', skillName: 'refund-policy', links: links('p1', V4) }];
const show = (props = {}) => renderRuntime(<AgentPanel open onClose={vi.fn()} agent={refund} window="24h" {...props} />);

beforeEach(() => {
  setSkillTracesOverride(true);
  hooks.agent.mockReturnValue(success({ agent: null, rows }));
});
afterEach(() => {
  cleanup();
  setSkillTracesOverride(null);
});

describe('AgentPanel', () => {
  it('reads endpoint 8 by the agent key and the caller window', () => {
    show();
    expect(hooks.agent).toHaveBeenCalledWith({ projectId: 'p1', service: 'refund-agent-svc', agent: 'refund-agent', window: '24h', from: undefined, to: undefined, enabled: true });
  });

  it('heads with the agent, project, service, context and connected app', () => {
    show();
    const panel = screen.getByRole('dialog', { name: 'refund-agent' });
    expect(within(panel).getByText('acme-support-production')).toBeInTheDocument();
    expect(within(panel).getByText('refund-agent-svc')).toBeInTheDocument();
    expect(within(panel).getByRole('link', { name: 'support-agent v2' })).toHaveAttribute('href', '/dashboard/skills/contexts/ctx-1');
    expect(within(panel).getByText('Connected app')).toBeInTheDocument();
  });

  it('lists the skills and versions it loaded, with numbers that open Observe', () => {
    show();
    expect(screen.getByRole('link', { name: 'refund-policy v4' })).toHaveAttribute('href', '/dashboard/skills/skill-a?tab=runtime');
    const traces = screen.getByRole('link', { name: 'refund-policy v4 traces: open traces' });
    expect(traces.getAttribute('href')).toMatch(/^\/dashboard\/observe\/p1\/llm-tracing\?/);
    expect(screen.getByRole('link', { name: 'refund-policy v4 failed requests: open traces' })).toBeInTheDocument();
  });

  it('names an unnamed agent by its project and offers Name it', () => {
    show({ agent: agentPanelInput(ROWS[2].agent) });
    expect(screen.getByRole('heading', { name: 'Unnamed agent in acme-sales' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Name it' }));
    expect(screen.getByText('OTEL_SERVICE_NAME=refund-agent')).toBeInTheDocument();
    expect(hooks.agent.mock.calls.at(-1)[0]).toMatchObject({ projectId: 'p2', agent: '' });
  });

  it('shows skeleton rows, an error with Retry, and a denied line', () => {
    hooks.agent.mockReturnValue(pending());
    const a = show();
    expect(screen.getByRole('progressbar', { name: "Loading this agent's skills" })).toBeInTheDocument();
    a.unmount();
    hooks.agent.mockReturnValue(failure('runtime_read_unavailable'));
    const b = show();
    expect(screen.getByText('Could not load runtime data. Your skill and versions are unchanged.')).toBeInTheDocument();
    b.unmount();
    hooks.agent.mockReturnValue(failure('project_not_readable', 403));
    show();
    expect(screen.getByText("You can't open this project.")).toBeInTheDocument();
  });

  it('reads nothing while closed', () => {
    show({ open: false });
    expect(hooks.agent.mock.calls.at(-1)[0].enabled).toBe(false);
  });
});
