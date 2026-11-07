import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react';
import PropTypes from 'prop-types';
import { MemoryRouter } from 'react-router-dom';
import { useSkillLabels, useSkillVersions } from 'src/api/skills';
import VersionSelect from './VersionSelect';
import { usePreviewState } from './SkillsStatePreview';

vi.mock('src/api/skills', () => ({ useSkillVersions: vi.fn(), useSkillLabels: vi.fn() }));
const refetch = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  useSkillVersions.mockReturnValue({ data: { results: [{ number: 2 }, { number: 1 }], count: 2, totalPages: 1 }, refetch });
  useSkillLabels.mockReturnValue({ data: [{ name: 'production', version: 1 }], refetch });
});
afterEach(cleanup);

it('offers versions, labels, latest and the draft with exact selector values', () => {
  const change = vi.fn();
  render(<VersionSelect skillId="id" value={2} includeDraft onChange={change} />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  expect(screen.getByRole('option', { name: 'Your draft' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Latest version' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('option', { name: 'production · v1' }));
  expect(change).toHaveBeenCalledWith('label:production');
});
it('keeps version numbers numeric and hides disabled optional selectors', () => {
  const change = vi.fn();
  render(<VersionSelect skillId="id" value={2} includeLabels={false} onChange={change} />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  expect(screen.queryByRole('option', { name: 'Your draft' })).not.toBeInTheDocument();
  expect(screen.queryByRole('option', { name: /production/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('option', { name: 'v1' }));
  expect(change).toHaveBeenCalledWith(1);
  expect(useSkillLabels).toHaveBeenCalledWith(null);
});
it('offers access to older pages without changing the selected version', () => {
  useSkillVersions.mockReturnValue({ data: { results: [{ number: 101 }], count: 101, totalPages: 2 }, refetch });
  const change = vi.fn();
  render(<VersionSelect skillId="id" value={101} onChange={change} />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(screen.getByRole('option', { name: 'Older versions' }));
  expect(useSkillVersions).toHaveBeenLastCalledWith('id', { page: 2, pageSize: 100 });
  expect(change).not.toHaveBeenCalled();
});
it('makes failed version loading retryable', () => {
  useSkillVersions.mockReturnValue({ isError: true, refetch });
  render(<VersionSelect skillId="id" value={1} onChange={vi.fn()} />);
  fireEvent.mouseDown(screen.getByRole('combobox'));
  fireEvent.click(screen.getByRole('option', { name: 'Retry loading versions' }));
  expect(refetch).toHaveBeenCalled();
});
it.each(['loading', 'error', 'empty', 'invalid'])('reads the preview state %s', (state) => {
  function Wrapper({ children }) { return <MemoryRouter initialEntries={[`/?state=${state}`]}>{children}</MemoryRouter>; }
  Wrapper.propTypes = { children: PropTypes.node };
  const { result } = renderHook(usePreviewState, { wrapper: Wrapper });
  expect(result.current).toBe(state === 'invalid' ? null : state);
});
