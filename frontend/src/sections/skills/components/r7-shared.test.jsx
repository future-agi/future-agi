import { StrictMode, useState } from 'react';
import PropTypes from 'prop-types';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Tab } from '@mui/material';
import { CustomTabs } from 'src/components/tabs/tabs';
import { enqueueSnackbar } from 'notistack';
import { RUN_STATUS } from 'src/utils/runStatus';
import IdentityChip from './IdentityChip';
import VersionIdentityRow from './VersionIdentityRow';
import VersionLabel from './VersionLabel';
import ObjectHeader from './ObjectHeader';
import PeoplePicker from './PeoplePicker';
import TechnicalDetails from './TechnicalDetails';
import EvidenceSummary from './EvidenceSummary';
import OperationFeedback from './OperationFeedback';
import CapabilityNotice from './CapabilityNotice';
import ActionReason, { reasonProps } from './ActionReason';
import { ScrollTable, stackedTableSx } from './responsiveTable';
import When from './When';
import { markTabFade, scrollTabsSx } from './tabStrip';
import CodeBlock from 'src/components/code-block/CodeBlock';
import { createTheme } from '@mui/material/styles';
import { coverageText, evidenceCoverage, evidenceOutcome, outcomeBadge, outcomeLabel } from '../utils/review-display';

vi.mock('notistack', () => ({ enqueueSnackbar: vi.fn() }));
const digest = `sha256:${'ab'.repeat(32)}`;
const people = [
  { id: 'reviewer-1', name: 'Noor', email: 'noor@example.invalid' },
  { id: 'reviewer-2', name: 'Alex', email: 'alex@example.invalid' },
];
// restoreAllMocks below also empties setupTests' matchMedia mock, which ObjectHeader's
// phone check reads; each test starts with a plain desktop matchMedia instead.
const desktopMedia = (query) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
beforeEach(() => { vi.clearAllMocks(); vi.stubGlobal('matchMedia', desktopMedia); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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
    expect(screen.getByText('3 of 4 runs graded · 1 regression · 1 human disagreement')).toBeVisible();
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
    expect(screen.getByText('Result not recorded; inspect before relying on it.')).toBeVisible();
    expect(screen.getByText('Not recorded')).toBeVisible();
    expect(screen.getAllByText('Not reported').length).toBeGreaterThan(0);
    expect(screen.queryByText('Passed')).not.toBeInTheDocument();
    expect(screen.queryByText(/0 of 0/)).not.toBeInTheDocument();
  });
  it('keeps explicit zero counts and never infers missing planned coverage', () => {
    render(<EvidenceSummary items={[{ ...item, counts: { graded: 0, regressed: 0, disagreements: 0 } }]} />);
    expect(screen.getByText('0 of Not reported runs graded · 0 regressions · 0 human disagreements')).toBeVisible();
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
// UX iteration 5, SH3: one phone layout for skill and context headers.
describe('ObjectHeader on a phone', () => {
  const header = () => <MemoryRouter><ObjectHeader crumbs={[{ label: 'Skills', to: '/dashboard/skills' }, { label: 'refund-policy' }]} title="refund-policy" purpose="Use when a customer asks for a refund on a damaged, defective or unwanted order." identity={<p>Viewing v2</p>} actions={<><button type="button">Edit latest v2</button><button type="button">Skill actions</button></>} tabs={<div role="tablist" aria-label="Skill sections" />} /></MemoryRouter>;
  const follows = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  it('orders title, purpose (two lines), identity, then the actions on their own left-aligned row', () => {
    vi.stubGlobal('matchMedia', (query) => ({ matches: /max-width/.test(query), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
    render(header());
    const order = [screen.getByRole('heading', { level: 1 }), screen.getByText(/^Use when a customer/), screen.getByText('Viewing v2'), screen.getByRole('button', { name: 'Edit latest v2' }), screen.getByRole('button', { name: 'Skill actions' }), screen.getByRole('tablist')];
    order.slice(1).forEach((node, index) => expect(follows(order[index], node)).toBe(true));
    const row = screen.getByRole('button', { name: 'Edit latest v2' }).closest('[data-object-actions]');
    expect(row).toContainElement(screen.getByRole('button', { name: 'Skill actions' }));
    expect(row).toHaveStyle({ justifyContent: 'flex-start', flexWrap: 'wrap' });
    // The purpose keeps to two lines; its full text stays available.
    const purpose = screen.getByText(/^Use when a customer/);
    expect(purpose).toHaveAttribute('title', 'Use when a customer asks for a refund on a damaged, defective or unwanted order.');
    const css = [...document.querySelectorAll('style')].map((node) => node.textContent).join('\n');
    expect(css).toMatch(/-webkit-line-clamp:\s*2/);
  });
  it('keeps the desktop header unchanged: actions beside the title, before the identity row', () => {
    const { container } = render(header());
    expect(follows(screen.getByRole('button', { name: 'Edit latest v2' }), screen.getByText('Viewing v2'))).toBe(true);
    expect(container.querySelector('[data-object-actions]')).toBeNull();
    expect(screen.getByText(/^Use when a customer/)).not.toHaveAttribute('title');
  });
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
  // A revoked key ended normally (UX iteration 5, SH7): neutral grey, never the error red.
  expect(RUN_STATUS.revoked).toEqual({ role: 'neutral', label: 'Revoked' });
});

describe('ActionReason', () => {
  it('explains a disabled primary and links it for assistive technology', () => {
    const reason = 'Enter an amount.';
    render(<div><button type="button" disabled {...reasonProps('save-reason', reason)}>Save</button><ActionReason id="save-reason" reason={reason} /></div>);
    expect(screen.getByRole('button', { name: 'Save' })).toHaveAccessibleDescription('Enter an amount.');
    expect(screen.getByText('Enter an amount.')).toBeVisible();
  });
  it('renders nothing and adds no dangling description without a reason', () => {
    const { container } = render(<ActionReason id="none" reason={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(reasonProps('none', null)).toEqual({});
  });
});

describe('responsive tables', () => {
  const theme = createTheme();
  it('stacks rows only below sm, hiding the header visually but not from assistive technology', () => {
    const sx = stackedTableSx(theme);
    const query = theme.breakpoints.down('sm');
    expect(Object.keys(sx)).toEqual([query]);
    expect(sx[query]['& thead']).toMatchObject({ position: 'absolute', overflow: 'hidden', clip: 'rect(0 0 0 0)' });
    expect(sx[query]['& thead'].display).toBeUndefined();
    expect(sx[query]['& tbody tr']).toMatchObject({ display: 'block', borderBottom: `1px solid ${theme.palette.divider}` });
    const title = Object.entries(sx[query]).find(([selector]) => selector.endsWith('.MuiTableCell-paddingCheckbox + td'))?.[1];
    expect(title).toMatchObject({ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', '& > *': { whiteSpace: 'normal' } });
    expect(sx[query]['& tbody td:not(:first-of-type)::before']).toEqual({ content: '" · "', whiteSpace: 'pre' });
    expect(sx[query]['& tbody td[data-cell="actions"]']).toMatchObject({ display: 'block' });
  });
  it('keeps a real, keyboard-scrollable table region with a hidden fade when nothing overflows', () => {
    render(<ScrollTable label="Versions"><table aria-label="Versions table"><tbody><tr><td>v1</td></tr></tbody></table></ScrollTable>);
    const region = screen.getByRole('region', { name: 'Versions' });
    expect(region).toHaveAttribute('tabindex', '0');
    expect(within(region).getByRole('table', { name: 'Versions table' })).toBeInTheDocument();
    expect(document.querySelector('[data-scroll-fade]')).toHaveAttribute('data-scroll-fade', 'off');
  });
});

describe('When', () => {
  it('shows a short time and reveals the full UTC time on keyboard focus', async () => {
    const user = userEvent.setup();
    render(<When value="2026-11-14T00:36:00Z" now={Date.parse('2026-11-14T00:48:30Z')} />);
    expect(screen.getByText('12 min ago')).toHaveAttribute('datetime', '2026-11-14T00:36:00.000Z');
    await user.tab();
    expect(screen.getByText('12 min ago')).toHaveFocus();
    expect(await screen.findByRole('tooltip')).toHaveTextContent('14 Nov 2026, 00:36 UTC');
  });
  it('reads a short expiry under an Expires header, with the full UTC time kept for the tooltip', () => {
    const now = Date.parse('2026-11-14T00:00:00Z');
    render(<><When value="2026-12-13T00:00:00Z" kind="expiry-short" now={now} /><When value="2026-11-12T00:00:00Z" kind="expiry-short" now={now} /><When value="2026-12-13T00:00:00Z" kind="expiry" now={now} /></>);
    expect(screen.getByText('in 29 days')).toHaveAttribute('datetime', '2026-12-13T00:00:00.000Z');
    expect(screen.getByText('expired 2 days ago')).toBeVisible();
    expect(screen.getByText('Expires in 29 days')).toBeVisible();
  });
  it('says Not reported for a missing time instead of inventing one', () => {
    render(<When value={undefined} />);
    expect(screen.getByText('Not reported')).toBeVisible();
  });
});

describe('evidence vocabulary', () => {
  it('reads an established pass on the saved test cases as "Passed on test cases"', () => {
    expect(outcomeLabel('works_on_examples')).toBe('Passed on test cases');
    expect(outcomeBadge('works_on_examples')).toEqual({ status: 'passed', label: 'Passed on test cases' });
    expect(outcomeBadge('failed')).toEqual({ status: 'failed' });
  });
  it('states coverage once, always in runs, never inferring missing counts', () => {
    expect(coverageText({ graded: 3, planned: 4, regressions: 1, incomplete: 1, disagreements: 0 })).toBe('3 of 4 runs graded · 1 incomplete · 1 regression · 0 human disagreements');
    expect(coverageText({})).toBe('Not reported of Not reported runs graded · Not reported regressions · Not reported human disagreements');
    expect(coverageText(null)).toBe('Not reported');
  });
  it('shows the result as a single badge per row, with one coverage string', () => {
    render(<EvidenceSummary items={[{ id: 'run-3', source: 'skills_text', title: 'Text checks', outcome: 'works_on_examples', counts: { graded: 2, planned: 2, regressed: 0, disagreements: 0 }, visibility: 'workspace' }]} />);
    const row = screen.getAllByRole('row')[1];
    expect(within(row).getByText('Passed on test cases')).toBeVisible();
    expect(within(row).queryByText(/Works on examples/)).not.toBeInTheDocument();
    expect(within(row).getAllByText(/runs graded/)).toHaveLength(1);
    expect(within(row).queryByText(/attempts/)).not.toBeInTheDocument();
  });
  it('maps every native outcome and text decision state to one badge, and never reads an unknown value as a pass', () => {
    const states = { works_on_examples: ['passed', 'Passed on test cases'], needs_changes: ['failed', 'Needs changes'], inconclusive: ['incomplete', 'Inconclusive'],
      diagnostic: ['diagnostic', 'Diagnostic only'], routing_only: ['routing_only', 'Routing only'] };
    for (const source of ['simulation_native', 'skills_text']) {
      for (const [value, [status, label]] of Object.entries(states)) expect(evidenceOutcome({ source, outcome: value })).toMatchObject({ status, label });
    }
    expect(evidenceOutcome({ source: 'skills_text', outcome: 'diagnostic' }).role).toBe('neutral');
    expect(evidenceOutcome({ source: 'skills_text', outcome: 'routing_only' }).role).toBe('neutral');
    // A text decision carried only in the summary reads the same as the option row.
    expect(evidenceOutcome({ source: 'skills_text', summary: { answer: 'works_on_examples' } })).toEqual(evidenceOutcome({ source: 'skills_text', outcome: 'works_on_examples' }));
    expect(evidenceOutcome({ source: 'simulation_native', summary: { answer: 'works_on_examples' } })).toBeNull();
    for (const value of ['works_on_example', 'release_ready', 'success', 'ok', 'true']) {
      const badge = evidenceOutcome({ source: 'skills_text', outcome: value });
      expect(badge).toEqual({ status: 'unknown', label: 'Unknown result' });
      expect(RUN_STATUS[badge.status].role).not.toBe('success');
    }
    expect(evidenceOutcome({ source: 'skills_text', outcome: null })).toBeNull();
    expect(evidenceOutcome({ source: 'simulation_native', outcome: 'fail' })).toEqual({ status: 'fail' });
  });
  it('states coverage in cases and runs only when the server reports a case count', () => {
    expect(evidenceCoverage({ graded: 2, planned: 2, regressed: 0, disagreements: 0 }, 1)).toBe('1 case tested · 2 runs graded · 0 regressions');
    expect(evidenceCoverage({ graded: 3, planned: 4, regressed: 1, disagreements: 2, cases: 2 })).toBe('2 cases tested · 3 of 4 runs graded · 1 regression · 2 human disagreements');
    // Without a case count it never derives one from runs.
    expect(evidenceCoverage({ graded: 2, planned: 2, regressed: 0, disagreements: 0 })).toBe('2 of 2 runs graded · 0 regressions · 0 human disagreements');
    expect(evidenceCoverage(null)).toBe('Not reported');
  });
  it('says "Not checked yet" for a result the review could not recheck, separate from "Not recorded"', () => {
    render(<EvidenceSummary items={[{ id: 'run-4', source: 'skills_text', title: 'Text checks', status: 'completed', outcome: 'works_on_examples', resultState: 'not_loaded', visibility: 'workspace' }, { id: 'run-5', source: 'skills_text', title: 'Older text checks', status: 'completed', visibility: 'workspace' }]} />);
    const [, loaded, recorded] = screen.getAllByRole('row');
    expect(within(loaded).getByText('Not checked yet')).toBeVisible();
    expect(within(loaded).getByText('Rechecked when you renew.')).toBeVisible();
    // The old wording pointed into the renewal dialog the user is already in.
    expect(within(loaded).queryByText('Renew the review to recheck this result.')).not.toBeInTheDocument();
    expect(within(loaded).queryByText('Passed on test cases')).not.toBeInTheDocument();
    expect(within(loaded).queryByText('Not recorded')).not.toBeInTheDocument();
    expect(within(recorded).getByText('Not recorded')).toBeVisible();
  });
  it('marks evidence carried from the test the user just ran, and shows visibility as a badge with whole words', () => {
    render(<EvidenceSummary items={[{ id: 'run-6', source: 'simulation_native', title: 'Application verification', outcome: 'works_on_examples', origin: 'from_test', suggested: true, visibility: 'workspace' }]} />);
    const row = screen.getAllByRole('row')[1];
    expect(within(row).getByText('From your test')).toBeVisible();
    expect(within(row).queryByText('Suggested')).not.toBeInTheDocument();
    const workspace = within(row).getByText('Workspace');
    expect(workspace.closest('td')).toHaveStyle({ whiteSpace: 'nowrap' });
    expect(screen.getByRole('columnheader', { name: 'Visibility' })).toHaveStyle({ whiteSpace: 'nowrap' });
  });
});

// jsdom has no layout: give a component's own box a width and run its ResizeObserver.
function withBoxWidth(width, run) {
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(() => ({ width, height: 40, top: 0, left: 0, right: width, bottom: 40, x: 0, y: 0, toJSON() {} }));
  const original = window.ResizeObserver;
  window.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  try { return run(); } finally { rect.mockRestore(); window.ResizeObserver = original; }
}

describe('evidence rows in a narrow container', () => {
  const items = [
    { id: 'run-7', source: 'skills_text', kind: 'text_comparison', outcome: 'works_on_examples', counts: { graded: 2, planned: 2, regressed: 0, disagreements: 0 }, visibility: 'workspace', expiresAt: '2026-11-21T00:00:00Z' },
    { id: 'run-8', source: 'simulation_native', kind: 'application_verification', resultState: 'not_loaded', visibility: 'private' },
  ];
  it('names kinds like Tests: "Answer check" and "Application test"', () => {
    render(<EvidenceSummary items={items} selectable onToggle={vi.fn()} onInspect={vi.fn()} />);
    expect(screen.getByRole('checkbox', { name: 'Attach Answer check' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Attach Application test' })).toBeDisabled();
    expect(screen.queryByText(/Text comparison|Application verification/)).not.toBeInTheDocument();
  });
  it('stacks rows under 720px: title and result, coverage, then visibility and expiry with a text Inspect', () => {
    withBoxWidth(390, () => {
      const inspect = vi.fn();
      render(<EvidenceSummary items={items} selectable onToggle={vi.fn()} onInspect={inspect} />);
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      const list = screen.getByRole('list', { name: 'Test evidence' });
      const [first, second] = within(list).getAllByRole('listitem');
      expect(within(first).getByText('Answer check')).toBeVisible();
      expect(within(first).getByText('Passed on test cases')).toBeVisible();
      expect(within(first).getByText('2 of 2 runs graded · 0 regressions · 0 human disagreements')).toBeVisible();
      // Separators sit only between parts: no line starts with " · ".
      for (const node of list.querySelectorAll('p, span')) expect(node.textContent.trimStart().startsWith('·')).toBe(false);
      expect(first).toHaveTextContent(/Workspace · Expire[sd] /);
      const button = within(first).getByRole('button', { name: 'Inspect' });
      expect(button).toHaveClass('MuiButton-text');
      expect(button).toHaveClass('MuiButton-sizeSmall');
      fireEvent.click(button);
      expect(inspect).toHaveBeenCalledWith(items[0]);
      // Same controls and names as the table, including the share rule.
      expect(within(first).getByRole('checkbox', { name: 'Attach Answer check' })).toBeEnabled();
      expect(within(second).getByRole('checkbox', { name: 'Attach Application test' })).toBeDisabled();
      expect(within(second).getByText('Not checked yet')).toBeVisible();
      expect(within(second).getByText('Rechecked when you renew.')).toBeVisible();
      expect(second).toHaveTextContent(/Private · Expiry not reported/);
      expect(within(second).getByText('Coverage not reported')).toBeVisible();
    });
  });
  it('keeps the table from 720px, with Inspect as a small text row action there too', () => {
    withBoxWidth(900, () => {
      render(<EvidenceSummary items={items} onInspect={vi.fn()} />);
      expect(screen.getByRole('table', { name: 'Test evidence' })).toBeVisible();
      expect(screen.getAllByRole('button', { name: 'Inspect' })[0]).toHaveClass('MuiButton-text');
      expect(screen.getAllByRole('button', { name: 'Inspect' })[0]).toHaveClass('MuiButton-sizeSmall');
    });
  });
  // UX iteration 5, SH5: no header word repeated in every cell, and no sentence repeated on every private row.
  it('reads short cells under their headers: "in 7 days" under Expires, and no visible text over the Inspect buttons', () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-14T00:00:00Z'));
    withBoxWidth(900, () => {
      render(<EvidenceSummary items={[items[0], { ...items[0], id: 'run-9', expiresAt: '2026-11-12T00:00:00Z' }]} onInspect={vi.fn()} />);
      const headers = screen.getAllByRole('columnheader');
      expect(headers.map((cell) => cell.textContent)).toEqual(['Evidence', 'Result', 'Coverage', 'Visibility', 'Expires', '']);
      expect(headers.at(-1)).toHaveAccessibleName('Actions');
      expect(screen.queryByRole('columnheader', { name: 'Inspect' })).not.toBeInTheDocument();
      expect(screen.getByText('in 7 days')).toBeVisible();
      expect(screen.getByText('expired 2 days ago')).toBeVisible();
      expect(screen.queryByText(/^Expires in|^Expired/)).not.toBeInTheDocument();
    });
  });
  it('keeps the long expiry in stacked rows, where no header names it', () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-11-14T00:00:00Z'));
    withBoxWidth(390, () => {
      render(<EvidenceSummary items={[items[0]]} onInspect={vi.fn()} />);
      expect(screen.getByText('Expires in 7 days')).toBeVisible();
    });
  });
  it('marks a private row with the Private badge only; the caller explains sharing once', () => {
    withBoxWidth(900, () => {
      render(<EvidenceSummary items={[{ ...items[0], visibility: 'private' }, { ...items[0], id: 'run-10', visibility: 'private' }]} selectable onToggle={vi.fn()} onInspect={vi.fn()} />);
      expect(screen.getAllByText('Private')).toHaveLength(2);
      expect(screen.queryByText(/Only you can see this result/)).not.toBeInTheDocument();
      // A private result still can't be attached until it is shared.
      screen.getAllByRole('checkbox', { name: 'Attach Answer check' }).forEach((box) => expect(box).toBeDisabled());
    });
  });
});

// UX iteration 5, SH4: the arrows show only while the tabs really overflow the strip.
describe('tab strip arrows', () => {
  // A Tabs root holding [arrow] scroller [arrow]; each shown arrow is `arrow` px wide and takes that from the scroller.
  function stripWith({ content, width, arrowsShown }) {
    const root = document.createElement('div');
    const scroller = document.createElement('div');
    const arrow = () => Object.assign(document.createElement('div'), { className: 'MuiTabs-scrollButtons', getBoundingClientRect: () => ({ width: arrowsShown ? 32 : 0 }) });
    root.append(arrow(), scroller, arrow());
    Object.defineProperty(scroller, 'scrollWidth', { value: content, configurable: true });
    Object.defineProperty(scroller, 'clientWidth', { value: width - (arrowsShown ? 64 : 0), configurable: true });
    Object.defineProperty(scroller, 'scrollLeft', { value: 0, configurable: true });
    scroller.getBoundingClientRect = () => ({ left: 0, right: scroller.clientWidth });
    return { root, scroller };
  }
  it('hides the arrows when every tab fits, as on rollout detail at 1440', () => {
    const { root, scroller } = stripWith({ content: 342, width: 1100, arrowsShown: true });
    markTabFade(scroller);
    expect(root).toHaveAttribute('data-tab-overflow', 'false');
  });
  it('does not let a shown arrow keep itself showing, nor count a sub-pixel difference', () => {
    const shown = stripWith({ content: 500, width: 500, arrowsShown: true });
    markTabFade(shown.scroller);
    expect(shown.root).toHaveAttribute('data-tab-overflow', 'false');
    const subpixel = stripWith({ content: 501, width: 500, arrowsShown: false });
    markTabFade(subpixel.scroller);
    expect(subpixel.root).toHaveAttribute('data-tab-overflow', 'false');
  });
  it('keeps the arrows while the tabs overflow, as on a phone', () => {
    const { root, scroller } = stripWith({ content: 520, width: 326, arrowsShown: false });
    markTabFade(scroller);
    expect(root).toHaveAttribute('data-tab-overflow', 'true');
  });
  it('hides the arrows only through the measured flag', () => {
    expect(scrollTabsSx['&[data-tab-overflow="false"] > .MuiTabs-scrollButtons']).toEqual({ display: 'none' });
  });
});

describe('tab strip fade', () => {
  // Alpha of a "linear-gradient(to right, …)" mask at x (px) in a strip `width` px wide.
  function alphaAt(gradient, x, width) {
    const body = gradient.replace(/^linear-gradient\(to right, /, '').replace(/\)$/, '');
    const stops = body.split(/,\s*(?![^(]*\))/).map((stop) => {
      const [, color, position] = stop.trim().match(/^(black|transparent)\s+(.+)$/);
      const calc = position.match(/^calc\(100% - (\d+)px\)$/);
      const at = calc ? width - Number(calc[1]) : position.endsWith('%') ? (width * parseFloat(position)) / 100 : parseFloat(position);
      return { at, alpha: color === 'black' ? 1 : 0 };
    });
    const after = stops.findIndex((stop) => stop.at >= x);
    if (after <= 0) return stops[Math.max(after, 0)].alpha;
    const a = stops[after - 1]; const b = stops[after];
    return b.at === a.at ? b.alpha : a.alpha + ((b.alpha - a.alpha) * (x - a.at)) / (b.at - a.at);
  }
  function strip(scrollLeft) {
    const scroller = document.createElement('div');
    const widths = [100, 90, 60, 80, 90, 80];
    let x = 0;
    for (const w of widths) {
      const tab = document.createElement('button'); tab.setAttribute('role', 'tab');
      const left = x; tab.getBoundingClientRect = () => ({ left: left - scrollLeft, right: left + w - scrollLeft, width: w });
      scroller.appendChild(tab); x += w;
    }
    Object.defineProperty(scroller, 'scrollLeft', { value: scrollLeft });
    Object.defineProperty(scroller, 'scrollWidth', { value: x });
    Object.defineProperty(scroller, 'clientWidth', { value: 300 });
    scroller.getBoundingClientRect = () => ({ left: 0, right: 300 });
    return scroller;
  }
  it('makes the whole cut fragment transparent at the end edge, after an 8px ramp', () => {
    const scroller = strip(190); // tabs 420-500 cut: 70px of it show at 230-300.
    markTabFade(scroller);
    const fade = scroller.style.getPropertyValue('--tab-fade');
    for (let x = 230; x <= 300; x += 5) expect(alphaAt(fade, x, 300)).toBe(0);
    expect(alphaAt(fade, 221, 300)).toBe(1);
  });
  it('mirrors it at the start edge and keeps no fade on a tab boundary', () => {
    const scroller = strip(130); // tab 100-190 is cut at the start: 60px of it show at 0-60.
    markTabFade(scroller);
    const fade = scroller.style.getPropertyValue('--tab-fade');
    for (let x = 0; x <= 60; x += 5) expect(alphaAt(fade, x, 300)).toBe(0);
    expect(alphaAt(fade, 69, 300)).toBe(1);
    const aligned = strip(100); // starts on a boundary (100) and ends inside 330-420 at 400.
    markTabFade(aligned);
    expect(aligned.style.getPropertyValue('--tab-fade')).toMatch(/^linear-gradient\(to right, black 0px, /);
  });
});

describe('CodeBlock in a narrow container', () => {
  it('puts Copy in a header row above the code, with the label as a caption and no reserved padding', () => {
    withBoxWidth(358, () => {
      render(<CodeBlock label="Context apply command" code={`python -m fi.context apply --plan '${'c'.repeat(80)}'`} copyLabel="Copy" ariaLabel="Copy apply command" />);
      const code = screen.getByLabelText('Context apply command');
      const root = code.parentElement;
      expect(root).toHaveAttribute('data-code-layout', 'header');
      const header = root.querySelector('[data-code-header]');
      expect(header.compareDocumentPosition(code) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(within(header).getByRole('button', { name: 'Copy apply command' })).toBeVisible();
      expect(within(header).getByText('Context apply command')).toBeVisible();
      expect(code).toHaveStyle({ paddingRight: '12px', overflowX: 'auto' });
    });
  });
  it('keeps a caller action in the header row with its own accessible name', () => {
    withBoxWidth(358, () => {
      render(<CodeBlock label="Context install plan command" code="claude --plan x" action={<button type="button" aria-label="Copy plan command">Copy</button>} />);
      const root = screen.getByLabelText('Context install plan command').parentElement;
      expect(within(root.querySelector('[data-code-header]')).getByRole('button', { name: 'Copy plan command' })).toBeVisible();
    });
  });
  it('floats the control from 600px, as before', () => {
    withBoxWidth(800, () => {
      render(<CodeBlock label="Command" code="echo hi" />);
      const root = screen.getByLabelText('Command').parentElement;
      expect(root).toHaveAttribute('data-code-layout', 'floating');
      expect(root.querySelector('[data-code-header]')).toBeNull();
      expect(screen.getByRole('button', { name: 'Copy code' })).toBeVisible();
    });
  });
});

describe('header identity', () => {
  it('puts the picker first as "Viewing [select]" and still names every role', () => {
    render(<VersionIdentityRow picker={<select aria-label="Viewed version"><option>v2</option></select>} viewing={{ number: 2, digest }} latest={{ number: 2 }} production={{ number: 1 }} draft={{ baseNumber: 2 }} />);
    const viewing = screen.getByText('Viewing');
    expect(viewing.compareDocumentPosition(screen.getByRole('combobox', { name: 'Viewed version' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText(/Viewing/)).toHaveLength(1);
    expect(screen.getByText('Production v1')).toBeVisible();
    expect(screen.getByText('Latest v2')).toBeVisible();
    // Production and latest wrap as one group (UX iteration 4, SH9).
    const group = screen.getByText('Latest v2').parentElement;
    expect(group).toContainElement(screen.getByText('Production v1'));
    expect(group).toHaveStyle({ flexWrap: 'nowrap' });
    expect(screen.getByText('Your draft (from v2)')).toBeVisible();
    // UX iteration 3 (SH3): no raw digest in the row; the viewed version's full digest is one keystroke away.
    expect(screen.queryByText(/abababab/)).not.toBeInTheDocument();
    expect(screen.queryByText('Digest')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'v2, show full version digest' })).toHaveAttribute('aria-haspopup', 'dialog');
  });
  it('reads "Viewing vN" without a picker and opens the full digest from the version label', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    render(<VersionIdentityRow viewing={{ number: 2, digest }} production={{ number: 1 }} />);
    const label = screen.getByRole('button', { name: 'v2, show full version digest' });
    expect(label).toHaveTextContent('v2');
    expect(screen.queryByText(/abababab/)).not.toBeInTheDocument();
    label.focus();
    await user.keyboard('{Enter}');
    const dialog = await screen.findByRole('dialog', { name: 'Full version digest of v2' });
    expect(within(dialog).getByText(digest)).toBeVisible();
    const copy = within(dialog).getByRole('button', { name: 'Copy full digest' });
    await waitFor(() => expect(copy).toHaveFocus());
    await user.keyboard('{Enter}');
    expect(write).toHaveBeenCalledWith(digest);
    expect(enqueueSnackbar).toHaveBeenCalledWith('Copied');
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(label).toHaveFocus();
  });
});

describe('VersionLabel', () => {
  it('shows the full digest on hover and copies the complete value from the popover', async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue();
    render(<VersionLabel number={3} digest={digest} label="candidate" suffix="· 41 min ago" />);
    const label = screen.getByRole('button', { name: 'v3, show full candidate digest' });
    expect(label).toHaveTextContent('v3 · 41 min ago');
    await user.hover(label);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(digest);
    await user.click(label);
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: 'Copy full digest' }));
    expect(write).toHaveBeenCalledWith(digest);
    expect(write.mock.calls[0][0]).toHaveLength(digest.length);
  });
  it('keeps the full value selectable when copying fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('Not allowed'));
    render(<VersionLabel number={1} digest={digest} />);
    await user.click(screen.getByRole('button', { name: 'v1, show full version digest' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Copy full digest' }));
    expect(await screen.findByText("Couldn't copy. Select the full value above.")).toBeVisible();
    expect(within(screen.getByRole('dialog')).getByText(digest)).toHaveStyle({ userSelect: 'all' });
  });
  it('renders plain text and no trigger when no digest is reported', () => {
    render(<VersionLabel number={4} />);
    expect(screen.getByText('v4')).toBeVisible();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    cleanup();
    render(<VersionLabel compact number={4} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('header identity, layout', () => {
  it('keeps the breadcrumb even when a caller hides direct breadcrumb children, and truncates the middle crumb', () => {
    render(<MemoryRouter><ObjectHeader sx={{ '& > .MuiBreadcrumbs-root': { display: 'none' } }} crumbs={[{ label: 'Reviews', to: '/dashboard/skills/reviews' }, { label: 'support-agent', to: '/x' }, { label: 'v2' }]} title="support-agent" /></MemoryRouter>);
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' });
    expect(nav).toBeVisible();
    expect(within(nav).getByRole('link', { name: 'support-agent' })).toHaveStyle({ textOverflow: 'ellipsis', whiteSpace: 'nowrap' });
  });
  it('makes object tabs scrollable without changing their names or values', () => {
    render(<MemoryRouter><ObjectHeader title="refund-policy" tabs={<CustomTabs value="versions" aria-label="Context detail sections"><Tab value="files" label="Files" /><Tab value="versions" label="Versions" /></CustomTabs>} /></MemoryRouter>);
    const tablist = screen.getByRole('tablist', { name: 'Context detail sections' });
    expect(tablist.closest('.MuiTabs-scrollableX')).not.toBeNull();
    expect(screen.getByRole('tab', { name: 'Versions' })).toHaveAttribute('aria-selected', 'true');
  });
});
