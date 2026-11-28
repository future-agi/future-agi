import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material/styles';
import { dsPalette, dsTypography } from 'src/theme/ds-tokens';
import { setSkillTracesOverride } from '../runtime/skillTracesFlag';
import AdoptionPanel, { AuthorizedProjectPicker, isAuthorizedProject, useAuthorizedProjects } from './AdoptionPanel';

// SKILL-TRACES §7.3: the panel is fetch data only, titled "Fetch adoption after a label move";
// SDK fetches (§6.7) read as channel "SDK"; the authorized project picker is exported for the
// Runtime tabs (lane ST10) and passes on only projects the person can open.

const mocks = vi.hoisted(() => ({ adoption: vi.fn(), projects: vi.fn() }));
vi.mock('src/api/skills/adoption', () => ({ useSkillAdoption: mocks.adoption }));
vi.mock('src/api/skills/testing', () => ({ useSkillV11Scope: () => ({ identity: 'org/ws/user' }), useSkillV11Query: mocks.projects }));
vi.mock('src/utils/axios', () => ({ endpoints: { project: { listProjects: () => '/authorized-projects/' } } }));
vi.mock('src/components/iconify', () => ({ default: () => null }));

const versionId = '11111111-2222-4333-8444-555555555555';
const at = '2026-11-06T18:00:00Z';
const success = (data) => ({ data, isPending: false, isFetching: false, isError: false, refetch: vi.fn() });
const row = { consumerId: 'a', consumerLabel: 'refund-agent key', channel: 'sdk', state: 'current_only', lastAt: at, versions: [{ version: 1, versionId, lastAt: at }] };
const observations = {
  target: { label: 'production', version: 1, versionId, promotedAt: '2026-10-30T18:00:00Z' },
  window: { start: '2026-10-30T18:00:00Z', end: at, asOf: at },
  summary: { currentOnly: 1, otherOnly: 0, mixed: 0, notSeen: 0, ambiguous: 0 },
  count: 1, page: 1, pageSize: 25, totalPages: 1, results: [row],
};
const skill = { id: 'skill-a', name: 'refund-policy' };
const projects = [{ id: 'project-a', name: 'acme-support-production' }, { id: 'project-b', name: 'acme-billing' }];
beforeEach(() => {
  vi.clearAllMocks();
  mocks.adoption.mockReturnValue(success(observations));
  mocks.projects.mockReturnValue(success(projects));
});
afterEach(() => { cleanup(); setSkillTracesOverride(null); });
const theme = createTheme({ palette: dsPalette('light'), typography: dsTypography });
const show = (ui) => render(<ThemeProvider theme={theme}><MemoryRouter>{ui}</MemoryRouter></ThemeProvider>);

describe('fetch adoption', () => {
  it('is titled by what it measures, with or without a production target', () => {
    const view = show(<AdoptionPanel skill={skill} days={7} />);
    expect(screen.getByText('Fetch adoption after a label move')).toBeInTheDocument();
    expect(screen.queryByText(/Who is using|Production fetches/)).not.toBeInTheDocument();
    view.unmount();
    mocks.adoption.mockReturnValue(success({ ...observations, target: null, results: [], count: 0 }));
    show(<AdoptionPanel skill={skill} days={7} />);
    expect(screen.getByText('Fetch adoption after a label move')).toBeInTheDocument();
    expect(screen.getByText('Production is not assigned')).toBeInTheDocument();
  });

  it('names an SDK consumer with the SDK channel', () => {
    mocks.adoption.mockReturnValue(success({ ...observations, results: [row, { ...row, consumerId: 'b', consumerLabel: 'SDK · ci key' }] }));
    show(<AdoptionPanel skill={skill} days={7} />);
    expect(screen.getByText('refund-agent key · SDK')).toBeInTheDocument();
    // A label that already carries its kind says it once.
    expect(screen.getByText('ci key · SDK')).toBeInTheDocument();
    expect(screen.queryByText(/· sdk$/)).not.toBeInTheDocument();
  });
});

describe('authorized project picker', () => {
  it('reads the authorized Observe projects through the Skills scoped query', () => {
    function Probe() { const query = useAuthorizedProjects(); return <output>{query.data.map((item) => item.name).join(',')}</output>; }
    show(<Probe />);
    const options = mocks.projects.mock.calls.at(-1)[0];
    expect(options.parts).toEqual(['traceProjects']);
    expect(options.params).toEqual({ project_type: 'observe' });
    expect(options.url()).toBe('/authorized-projects/');
    expect(options.mapper({ projects })).toBe(projects);
    expect(screen.getByText('acme-support-production,acme-billing')).toBeInTheDocument();
  });

  it('checks ids against the loaded projects only', () => {
    expect(isAuthorizedProject(success(projects), 'project-a')).toBe(true);
    expect(isAuthorizedProject(success(projects), 'project-z')).toBe(false);
    expect(isAuthorizedProject({ ...success(projects), isError: true }, 'project-a')).toBe(false);
    expect(isAuthorizedProject({ data: undefined }, 'project-a')).toBe(false);
  });

  it('single: never shows or passes on a project the person cannot open', async () => {
    const onChange = vi.fn();
    show(<AuthorizedProjectPicker value="project-z" onChange={onChange} />);
    const select = screen.getByRole('combobox', { name: 'Project' });
    expect(select).not.toHaveTextContent('project-z');
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole('option', { name: 'acme-billing' }));
    expect(onChange).toHaveBeenLastCalledWith('project-b');
  });

  it('multiple: reads "All projects you can open" when none is chosen, and returns ids', async () => {
    function Multi() {
      const [value, setValue] = useState([]);
      return <><AuthorizedProjectPicker multiple label="Projects" value={value} onChange={setValue} /><output aria-label="Chosen">{value.join(',')}</output></>;
    }
    show(<Multi />);
    const select = screen.getByRole('combobox', { name: 'Projects' });
    expect(select).toHaveTextContent('All projects you can open');
    fireEvent.mouseDown(select);
    fireEvent.click(await screen.findByRole('option', { name: 'acme-support-production' }));
    expect(screen.getByLabelText('Chosen')).toHaveTextContent('project-a');
    expect(select).toHaveTextContent('acme-support-production');
  });

  it.each([
    ['loading', { data: undefined, isPending: true, isError: false }, 'Loading authorized projects'],
    ['error', { data: undefined, isPending: false, isError: true, refetch: vi.fn() }, 'Could not load authorized projects'],
    ['empty', success([]), 'No accessible Observe projects'],
  ])('shows the %s state', (_name, query, text) => {
    mocks.projects.mockReturnValue(query);
    show(<AuthorizedProjectPicker value="" onChange={vi.fn()} />);
    expect(screen.queryAllByText(text).length + screen.queryAllByLabelText(text).length).toBeGreaterThan(0);
  });
});

// SKILL-TRACES §7.3 "Activity tab", §7.8 and UX-02 (UXF5 commit 2): with `skillTraces` on, the
// legacy fetch-adoption link opens Observe in the same tab; with it off it keeps opening a new tab.
describe('fetch adoption trace link', () => {
  function Location() {
    const location = useLocation();
    return <output aria-label="Location">{`${location.pathname}${location.search}`}</output>;
  }
  const openTraces = async () => {
    render(<ThemeProvider theme={theme}><MemoryRouter initialEntries={['/dashboard/skills/skill-a?tab=usage']}><AdoptionPanel skill={skill} days={7} /><Location /></MemoryRouter></ThemeProvider>);
    fireEvent.click(screen.getByRole('button', { name: 'View traces' }));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: 'Project' }));
    fireEvent.click(await screen.findByRole('option', { name: 'acme-support-production' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open traces' }));
  };

  it('navigates in the same tab with skillTraces on, so Back returns here', async () => {
    setSkillTracesOverride(true);
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await openTraces();
    expect(open).not.toHaveBeenCalled();
    const location = screen.getByLabelText('Location').textContent;
    expect(location).toMatch(/^\/dashboard\/observe\/project-a\/llm-tracing\?/);
    expect(decodeURIComponent(location)).toContain(versionId);
    open.mockRestore();
  });

  it('keeps the new-tab link with skillTraces off', async () => {
    setSkillTracesOverride(false);
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    await openTraces();
    expect(open).toHaveBeenCalledWith(expect.stringContaining('/project-a/llm-tracing?'), '_blank', 'noopener,noreferrer');
    expect(screen.getByLabelText('Location')).toHaveTextContent('/dashboard/skills/skill-a?tab=usage');
    open.mockRestore();
  });
});
