import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import RuntimeDiagnosis, { RuntimeNotes } from './RuntimeDiagnosis';
import { SKILL, renderRuntime } from './__tests__/fixtures';

vi.mock('src/components/iconify', () => ({ default: () => null }));

const consumer = (label, extra = {}) => ({ label, channel: 'sdk', sdkVersion: '0.9.2', tracingMode: 'on', canRecord: false, ...extra });
const show = (diagnosis, props = {}) => renderRuntime(<RuntimeDiagnosis empty diagnosis={diagnosis} subjectId={SKILL.id} {...props} />);

afterEach(cleanup);

// Every §7.6 row (UX-07). "No loads recorded" never appears without a reason.
describe('RuntimeDiagnosis', () => {
  it('workspace has no tracing project', () => {
    show({ state: 'no_tracing_project', consumers: [] });
    expect(screen.getByText('No loads recorded')).toBeInTheDocument();
    expect(screen.getByText('Loads are recorded in traces. This workspace has no tracing project yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Set up tracing' })).toHaveAttribute('href', '/dashboard/observe');
  });

  it('never fetched', () => {
    show({ state: 'never_fetched', consumers: [] });
    expect(screen.getByText('Nobody has fetched this skill yet.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Install' })).toHaveAttribute('href', '/dashboard/skills/skill-a?tab=use');
  });

  it('fetched by consumers on an older SDK, listing each with its version', () => {
    show({ state: 'sdk_cannot_record', minSdkVersion: '1.4.0', consumers: [consumer('ci-key'), consumer('prod-key', { sdkVersion: '0.8.0' }), consumer('dev-key')] });
    expect(screen.getByText('3 consumers fetched this skill in the last 7 days. None can record loads: they use an SDK older than 1.4.0.')).toBeInTheDocument();
    expect(screen.getByText('prod-key · SDK · SDK 0.8.0 · tracing on')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Upgrade steps' })).not.toHaveAttribute('target');
  });

  it('fetched with tracing off or no OpenTelemetry, with the line that fixes each', () => {
    show({ state: 'tracing_unavailable', consumers: [
      consumer('a', { sdkVersion: '1.4.0', tracingMode: 'off' }),
      consumer('b', { sdkVersion: '1.4.0', tracingMode: 'off' }),
      consumer('c', { sdkVersion: '1.4.0', tracingMode: 'no_otel' }),
    ] });
    expect(screen.getByText('3 consumers fetched this skill in the last 7 days. 2 have tracing switched off, 1 has no OpenTelemetry set up.')).toBeInTheDocument();
    expect(screen.getAllByText(/Remove FI_SKILLS_TRACING=off/)).toHaveLength(2);
    expect(screen.getByText(/Call traceAI register\(\)/)).toBeInTheDocument();
  });

  it('fetched only through MCP, download or the raw API', () => {
    show({ state: 'non_sdk_fetches', fetches: 212, consumers: [{ label: 'm', channel: 'mcp', canRecord: false }, { label: 'd', channel: 'download', canRecord: false }] });
    expect(screen.getByText('Fetched 212 times through MCP and download. Loads are recorded only from the Python or TypeScript SDK.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Docs' })).toBeInTheDocument();
  });

  it('SDK can record, no loads, offering Check tracing', () => {
    const onCheckTracing = vi.fn();
    show({ state: 'awaiting_loads', consumers: [consumer('k', { canRecord: true })] }, { onCheckTracing });
    expect(screen.getByText('Consumers can record loads, but none arrived in this window.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Check tracing' }));
    expect(onCheckTracing).toHaveBeenCalled();
  });

  it('some consumers record and some cannot: one line above the table, with See which', () => {
    renderRuntime(<RuntimeDiagnosis diagnosis={{ state: 'partially_recording', consumers: [consumer('x'), consumer('y'), consumer('z'), consumer('w', { canRecord: true })] }} loadsAgents={2} />);
    expect(screen.queryByText('No loads recorded')).not.toBeInTheDocument();
    expect(screen.getByText("Loads recorded from 2 agents. 3 other consumers fetched this skill and can't record loads.")).toBeInTheDocument();
    expect(screen.queryByText(/^x ·/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'See which' }));
    expect(screen.getByText(/^x ·/)).toBeInTheDocument();
  });

  it('no readable project', () => {
    show({ state: 'no_readable_projects', consumers: [] });
    expect(screen.getByText("You can't open the projects where this skill is loaded. Ask a workspace admin for access.")).toBeInTheDocument();
  });

  it('an unknown state still explains itself', () => {
    show({ state: 'something_new', consumers: [] });
    expect(screen.getByText(/the reason couldn't be worked out/)).toBeInTheDocument();
  });

  it('a recording workspace shows no diagnosis', () => {
    renderRuntime(<RuntimeDiagnosis diagnosis={{ state: 'recording', consumers: [] }} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('agent contexts use their own word', () => {
    show({ state: 'never_fetched', consumers: [] }, { kind: 'agent_context' });
    expect(screen.getByText('Nobody has fetched this agent context yet.')).toBeInTheDocument();
  });
});

describe('RuntimeNotes', () => {
  it('reports the project cap, unreadable projects, missing roots and no evals without naming projects', () => {
    renderRuntime(<RuntimeNotes coverage={{ projectsScanned: 50, projectsTotal: 63, unreadableProjects: 2 }} truncated incomplete={12} noEvals onChooseProjects={() => {}} />);
    expect(screen.getByText(/Showing 50 of 63 projects\./)).toBeInTheDocument();
    expect(screen.getByText("2 projects you can't open aren't included.")).toBeInTheDocument();
    expect(screen.getByText("12 traces have no root span yet and aren't counted.")).toBeInTheDocument();
    expect(screen.getByText('No evals ran on these traces.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose projects' })).toBeInTheDocument();
  });
});
