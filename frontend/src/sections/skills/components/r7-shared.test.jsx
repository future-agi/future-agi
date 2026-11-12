import { StrictMode, useState } from 'react';
import PropTypes from 'prop-types';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { enqueueSnackbar } from 'notistack';
import { RUN_STATUS } from 'src/utils/runStatus';
import IdentityChip from './IdentityChip';
import VersionIdentityRow from './VersionIdentityRow';
import ObjectHeader from './ObjectHeader';
import PeoplePicker from './PeoplePicker';
import TechnicalDetails from './TechnicalDetails';
import EvidenceSummary from './EvidenceSummary';
import OperationFeedback from './OperationFeedback';
import CapabilityNotice from './CapabilityNotice';

vi.mock('notistack', () => ({ enqueueSnackbar: vi.fn() }));
const digest = `sha256:${'ab'.repeat(32)}`;
const people = [
  { id: 'reviewer-1', name: 'Noor', email: 'noor@example.invalid' },
  { id: 'reviewer-2', name: 'Alex', email: 'alex@example.invalid' },
];
beforeEach(() => vi.clearAllMocks());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('IdentityChip', () => {
  it('shortens presentation but copies the full original digest with a labelled action', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    render(<IdentityChip value={digest} label="Candidate digest" />);
    expect(screen.getByText('abababababab…')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Copy full Candidate digest' }));
    expect(write).toHaveBeenCalledWith(digest);
    expect(enqueueSnackbar).toHaveBeenCalledWith('Copied');
    expect(screen.queryByText(digest)).not.toBeInTheDocument();
  });
  it('makes full identity reachable by keyboard and truncates UUIDs to eight characters', async () => {
    const user = userEvent.setup();
    const value = '01234567-89ab-4cde-8fab-0123456789ab';
    render(<IdentityChip value={value} kind="id" label="Version" />);
    await user.tab();
    expect(screen.getByText('01234567…')).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent(value);
  });
  it('keeps a failed copy inline with selectable bytes and no success toast', async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('Not allowed'));
    const { rerender } = render(<IdentityChip value={digest} />);
    await user.click(screen.getByRole('button', { name: 'Copy full identifier' }));
    expect(await screen.findByRole('status')).toHaveTextContent("Couldn't copy. Select the full value in Details.");
    expect(within(screen.getByRole('status')).getByText(digest).tagName).toBe('CODE');
    expect(enqueueSnackbar).not.toHaveBeenCalled();
    rerender(<IdentityChip value="sha256:other" />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
  it('shows missing identity honestly and offers no copy action', () => {
    render(<IdentityChip label="Version" />);
    expect(screen.getByText('Not reported')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('TechnicalDetails', () => {
  it('supports keyboard disclosure with a persistent aria-controls target', async () => {
    const user = userEvent.setup();
    render(<TechnicalDetails><pre>Full record</pre></TechnicalDetails>);
    const trigger = screen.getByRole('button', { name: 'Details' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    expect(document.getElementById(trigger.getAttribute('aria-controls'))).not.toBeVisible();
    await user.tab();
    await user.keyboard('{Enter}');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('region', { name: 'Details' })).toHaveTextContent('Full record');
    await user.keyboard(' ');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });
  it('supports an initially open, specifically named disclosure', () => {
    render(<TechnicalDetails title="Audit details" defaultOpen>Audit record</TechnicalDetails>);
    expect(screen.getByRole('button', { name: 'Audit details' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Audit record')).toBeVisible();
  });
});

function ControlledPicker({ onChange, ...props }) {
  const [value, setValue] = useState('');
  return <PeoplePicker {...props} value={value} onChange={(id) => { setValue(id); onChange(id); }} />;
}
ControlledPicker.propTypes = { onChange: PropTypes.func.isRequired };

describe('PeoplePicker', () => {
  it('preserves frozen combobox and option names and shows the selected email separately', async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(<ControlledPicker people={people} label="Assigned reviewer" onChange={change} />);
    const combo = screen.getByRole('combobox', { name: 'Assigned reviewer' });
    expect(combo).toHaveTextContent('Choose an authorized reviewer');
    await user.click(combo);
    expect(screen.getByRole('option', { name: 'Choose an authorized reviewer' })).toBeVisible();
    await user.click(screen.getByRole('option', { name: 'Noor', exact: true }));
    expect(change).toHaveBeenCalledWith('reviewer-1');
    expect(screen.getByRole('combobox', { name: 'Assigned reviewer' })).toHaveTextContent('Noor');
    expect(screen.getByText('noor@example.invalid')).toBeVisible();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
  it('selects a sole reviewer once, even in strict mode, without submitting', async () => {
    const change = vi.fn();
    const submit = vi.fn();
    render(<StrictMode><form onSubmit={submit}><ControlledPicker people={[people[0]]} label="Reviewer" onChange={change} /></form></StrictMode>);
    await waitFor(() => expect(change).toHaveBeenCalledExactlyOnceWith('reviewer-1'));
    expect(screen.getByRole('combobox', { name: 'Reviewer' })).toHaveTextContent('Noor');
    expect(submit).not.toHaveBeenCalled();
  });
  it('does not auto-select while loading, disabled, failed, or explicitly opted out', () => {
    const change = vi.fn();
    const { rerender } = render(<PeoplePicker people={[people[0]]} label="Reviewer" onChange={change} loading />);
    expect(screen.getByRole('combobox', { name: 'Reviewer' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('Loading people…')).toBeVisible();
    expect(screen.queryByText(/No other eligible/)).not.toBeInTheDocument();
    rerender(<PeoplePicker people={[people[0]]} label="Reviewer" onChange={change} disabled />);
    rerender(<PeoplePicker people={[people[0]]} label="Reviewer" onChange={change} error={new Error('Offline')} />);
    rerender(<PeoplePicker people={[people[0]]} label="Reviewer" onChange={change} autoSelectSole={false} />);
    expect(change).not.toHaveBeenCalled();
  });
  it('distinguishes error with retry from a successful empty result', () => {
    const retry = vi.fn();
    const { rerender } = render(<PeoplePicker label="Reviewer" onChange={vi.fn()} error={new Error('Offline')} onRetry={retry} />);
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load reviewers.");
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.queryByText(/No other eligible/)).not.toBeInTheDocument();
    rerender(<PeoplePicker label="Reviewer" onChange={vi.fn()} />);
    expect(screen.getByText('No other eligible reviewers in this workspace. Ask an admin to give someone review access.')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
  it('never silently replaces a selection removed from eligibility', () => {
    const change = vi.fn();
    render(<PeoplePicker people={[people[1]]} value="reviewer-1" label="Reviewer" onChange={change} />);
    expect(screen.getByRole('combobox', { name: 'Reviewer' })).toHaveTextContent('Reviewer no longer available');
    expect(change).not.toHaveBeenCalled();
  });
});

describe('OperationFeedback', () => {
  it('shows permission failures beside the action without offering a retry', () => {
    render(<OperationFeedback error={{ statusCode: 403, code: 'permission_denied' }} action="release this version" onRetry={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('You no longer have permission to release this version.');
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.queryByText('permission_denied')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Details' }));
    expect(screen.getByText('permission_denied')).toBeVisible();
  });
  it.each(['stale_base', 'review_stale', 'target_configuration_changed', 'annotation_conflict', 'evidence_expired'])('requires refresh for %s', (code) => {
    const refresh = vi.fn();
    render(<OperationFeedback error={{ code }} onRefresh={refresh} />);
    expect(screen.getByRole('alert')).toHaveTextContent('This changed since you opened it. Refresh and review again.');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(refresh).toHaveBeenCalledOnce();
  });
  it('normalizes raw Axios errors without confusing transport and semantic codes', () => {
    render(<OperationFeedback error={{ code: 'ERR_BAD_REQUEST', response: { status: 403, data: { code: 'evidence_share_required' } } }} onRefresh={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeVisible();
  });
  it('offers retry only for a known pre-send transport failure', () => {
    const retry = vi.fn();
    render(<OperationFeedback error={{ code: 'retryable_transport' }} onRetry={retry} />);
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't reach the server. Nothing was changed.");
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledOnce();
  });
  it.each([
    { code: 'outcome_unknown', idempotencyKey: 'original-key' },
    { code: 'ERR_NETWORK', idempotencyKey: 'original-key' },
    { statusCode: 503, idempotencyKey: 'original-key' },
    { statusCode: 403, uncertain: true, idempotencyKey: 'original-key' },
  ])('reconciles uncertain outcomes with the original key, never retries ($code / $statusCode)', (error) => {
    const reconcile = vi.fn(); const retry = vi.fn();
    render(<OperationFeedback error={error} onReconcile={reconcile} onRetry={retry} />);
    expect(screen.getByRole('alert')).toHaveTextContent("We couldn't confirm whether this went through. Check before trying again.");
    fireEvent.click(screen.getByRole('button', { name: 'Check status' }));
    expect(reconcile).toHaveBeenCalledWith('original-key');
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(retry).not.toHaveBeenCalled();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });
  it('keeps validation failures distinct from permission and uncertain outcomes', () => {
    render(<OperationFeedback error={{ statusCode: 422, code: 'checks_failed' }} />);
    expect(screen.getByRole('alert')).toHaveTextContent("This couldn't be completed. Review the details before trying again.");
    expect(screen.queryByRole('button', { name: 'Check status' })).not.toBeInTheDocument();
  });
  it('renders nothing after the originating error clears', () => {
    const { container } = render(<OperationFeedback />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('EvidenceSummary', () => {
  const item = { id: 'run-1', source: 'simulation_native', kind: 'application_verification', title: 'Refund checks', candidateNumber: 2, baselineNumber: 1, status: 'passed', counts: { graded: 3, planned: 4, regressed: 1, disagreements: 1 }, visibility: 'private' };
  it('reports exact coverage and visibility and forwards original records for controlled actions', () => {
    const inspect = vi.fn(); const toggle = vi.fn();
    render(<EvidenceSummary items={[item]} selectable selected={['simulation_native:run-1']} onToggle={toggle} onInspect={inspect} />);
    expect(screen.getByText('3 of 4 cases · 1 regression · 1 human disagreement')).toBeVisible();
    expect(screen.getByText('Private')).toBeVisible();
    expect(screen.getByRole('checkbox', { name: 'Attach Refund checks' })).toBeChecked();
    fireEvent.click(screen.getByRole('checkbox'));
    expect(toggle).toHaveBeenCalledWith(item);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect' }));
    expect(inspect).toHaveBeenCalledWith(item);
    expect(item.visibility).toBe('private');
  });
  it('never substitutes zero or a pass for unreported fields', () => {
    render(<EvidenceSummary items={[{ id: 'run-2' }]} />);
    expect(screen.getAllByText('Not reported').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText('Passed')).not.toBeInTheDocument();
    expect(screen.queryByText(/0 of 0/)).not.toBeInTheDocument();
  });
  it('keeps explicit zero counts and never infers missing planned coverage', () => {
    render(<EvidenceSummary items={[{ ...item, counts: { graded: 0, regressed: 0, disagreements: 0 } }]} />);
    expect(screen.getByText('0 of Not reported cases · 0 regressions · 0 human disagreements')).toBeVisible();
  });
  it('keeps evidence with the same ID from different sources separate', () => {
    render(<EvidenceSummary items={[item, { ...item, source: 'skills_text', title: 'Text checks' }]} selectable selected={new Set(['simulation_native:run-1'])} onToggle={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: 'Attach Refund checks' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Attach Text checks' })).not.toBeChecked();
  });
  it('explains that no evidence is not a pass', () => {
    render(<EvidenceSummary />);
    expect(screen.getByText('No matching test evidence. Not tested is not a pass.')).toBeVisible();
  });
});

it('keeps viewing, latest and production roles distinct even at the same version', () => {
  render(<VersionIdentityRow viewing={{ number: 2, digest }} latest={{ number: 2 }} production={{ number: 2 }} draft={{ baseNumber: 1 }} extra={<a href="?tab=consumers">Delivery: see Consumers</a>} />);
  expect(screen.getByText(/Viewing/)).toHaveTextContent('Viewing v2');
  expect(screen.getByText('Latest v2')).toBeVisible();
  expect(screen.getByText('Production v2')).toBeVisible();
  expect(screen.getByText('Your draft (from v1)')).toBeVisible();
  expect(screen.getByRole('link', { name: 'Delivery: see Consumers' })).toBeVisible();
});
it('does not claim production when no production version is supplied', () => {
  render(<VersionIdentityRow viewing={{ number: 1 }} />);
  expect(screen.getByText('No production')).toBeVisible();
});
it('renders object navigation, purpose, identity and tabs in hierarchy order', () => {
  render(<MemoryRouter><ObjectHeader crumbs={[{ label: 'Skills', to: '/dashboard/skills' }, { label: 'refund-policy' }]} title="refund-policy" purpose="Handle refunds" identity={<VersionIdentityRow viewing={{ number: 1 }} />} actions={<button>Edit viewed v1</button>} tabs={<div role="tablist" aria-label="Skill sections" />} /></MemoryRouter>);
  expect(screen.getByRole('link', { name: 'Skills' })).toHaveAttribute('href', '/dashboard/skills');
  expect(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByText('refund-policy')).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('refund-policy');
  expect(screen.getByText('Handle refunds')).toBeVisible();
  expect(screen.getByRole('tablist', { name: 'Skill sections' })).toBeInTheDocument();
});
it('treats capability off as neutral information, with optional details', () => {
  render(<CapabilityNotice title="Repository rollouts are off" description="Existing records stay readable." actions={<button>Open records</button>} details="Technical configuration" />);
  expect(screen.getByRole('heading', { name: 'Repository rollouts are off' })).toBeVisible();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.queryByText('Technical configuration')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Details' }));
  expect(screen.getByText('Technical configuration')).toBeVisible();
});
it('adds the shared vocabulary without merging approval, release or delivery', () => {
  expect(RUN_STATUS.approved).toEqual({ role: 'success', label: 'Approved' });
  expect(RUN_STATUS.released).toEqual({ role: 'success', label: 'Released' });
  expect(RUN_STATUS.delivered.label).toBe('Delivered');
  expect(RUN_STATUS.not_observed).toEqual({ role: 'neutral', label: 'Not observed', shape: 'hollow' });
  expect(RUN_STATUS.checking).toEqual({ role: 'info', label: 'Checking', shape: 'live' });
  for (const key of ['pinned', 'stale', 'superseded', 'held', 'excluded', 'update_available', 'up_to_date', 'unavailable', 'private']) expect(RUN_STATUS[key]).toBeDefined();
});
