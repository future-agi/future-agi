import { cleanup, render as rtlRender, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import ContextVersionsTable from './ContextVersionsTable';
vi.mock('./ContextVersionCompare', () => ({ default: ({ from, to, onClose }) => <section aria-label="Comparison"><p>{`Comparing v${from} and v${to}`}</p><button type="button" onClick={onClose}>Close compare</button></section> }));
// The release control is lane UXF2's ghost button; here it only has to sit in the right row and cell.
vi.mock('./ContextLabelRequestPanel', () => ({ default: ({ version }) => <button type="button" data-version={version.number}>{`Request release for v${version.number}`}</button> }));
const REQUEST = /^Request release for v\d+$/;
afterEach(cleanup);
function Params() { const [params] = useSearchParams(); return <output data-testid="params">{params.toString()}</output>; }
const render = (ui, entry = '/') => rtlRender(<MemoryRouter initialEntries={[entry]}>{ui}<Params /></MemoryRouter>);
const rows = [2, 1].map((number) => ({ id: `v${number}`, number, note: `Note ${number}`, createdBy: { name: 'Author' }, fileCount: number + 2, labels: number === 1 ? ['production'] : [] }));
const props = { contextId: 'context', latestVersion: 2, query: { data: { results: rows, count: 2, pageSize: 100 } }, page: 1, onPageChange: vi.fn(), onSelectVersion: vi.fn() };
it('shows version metadata and requests a release only above production', () => {
  render(<ContextVersionsTable {...props} productionVersion={1} />);
  expect(screen.getByRole('table', { name: 'Context versions' })).toBeVisible();
  expect(screen.getAllByRole('button', { name: REQUEST })).toHaveLength(1);
  expect(screen.getByRole('button', { name: REQUEST })).toHaveAttribute('data-version', '2');
  expect(screen.getByText('Production')).toBeVisible();
  expect(screen.queryByText('v1 is in production.')).not.toBeInTheDocument();
  expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent).filter(Boolean)).toEqual(['Version', 'Note', 'Author', 'Published', 'Labels', 'Files', 'Actions']);
  // The release control lives in the row's actions cell, before the menu; there is no Release column.
  const requestCell = screen.getByRole('button', { name: REQUEST }).closest('td');
  expect(requestCell).toHaveAttribute('data-column', 'actions');
  expect(within(requestCell).getByRole('button', { name: 'Actions for v2' })).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Rows per page' })).toBeVisible();
});
it('offers exactly one latest candidate before first production, not every historical version', () => {
  render(<ContextVersionsTable {...props} />);
  expect(screen.getAllByRole('button', { name: REQUEST })).toHaveLength(1);
  expect(screen.getByRole('button', { name: REQUEST })).toHaveAttribute('data-version', '2');
});
it('does not offer a forward release from production to itself or an older version', () => {
  render(<ContextVersionsTable {...props} productionVersion={2} />);
  expect(screen.queryByRole('button', { name: REQUEST })).not.toBeInTheDocument();
  expect(screen.queryByText('v2 is in production.')).not.toBeInTheDocument();
});

it('keeps the release request scoped to the viewed candidate when several publications are above production', () => {
  render(<ContextVersionsTable {...props} latestVersion={3} viewedNumber={2} productionVersion={1} query={{ data: { ...props.query.data, count: 3, results: [{ ...rows[0], id: 'v3', number: 3 }, ...rows] } }} />);
  expect(screen.getAllByRole('button', { name: REQUEST })).toHaveLength(1);
  expect(screen.getByRole('button', { name: REQUEST })).toHaveAttribute('data-version', '2');
});

it('tags only the viewed row, lists file counts, and replaces the grey View text with row click and a menu', async () => {
  const onSelectVersion = vi.fn();
  render(<ContextVersionsTable {...props} productionVersion={1} viewedNumber={1} onSelectVersion={onSelectVersion} />);
  expect(screen.getAllByText('Viewing')).toHaveLength(1);
  expect(screen.getByText('Viewing').closest('tr')).toHaveTextContent('Note 1');
  expect(screen.queryByRole('button', { name: 'View v2' })).not.toBeInTheDocument();
  const row = screen.getByText('Note 2').closest('tr');
  expect(within(row).getByText('4')).toBeVisible();
  const user = userEvent.setup();
  await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
  await user.click(screen.getByRole('menuitem', { name: 'View' }));
  expect(onSelectVersion).toHaveBeenCalledWith(2);
});
it('picks two versions with checkboxes and opens the comparison from one outlined button', async () => {
  const user = userEvent.setup();
  render(<ContextVersionsTable {...props} productionVersion={1} />, '/?tab=versions');
  expect(screen.getByText('Select two versions to compare.')).toBeVisible();
  expect(screen.queryByRole('button', { name: /^Compare v/ })).not.toBeInTheDocument();
  await user.click(screen.getByRole('checkbox', { name: 'Select v2 to compare' }));
  expect(screen.getByText('Select two versions to compare.')).toBeVisible();
  await user.click(screen.getByRole('checkbox', { name: 'Select v1 to compare' }));
  const compare = screen.getByRole('button', { name: 'Compare v1 and v2' });
  expect(compare).toHaveClass('MuiButton-outlined');
  await user.click(compare);
  expect(screen.getByTestId('params')).toHaveTextContent('tab=versions&compare=1%2C2');
  expect(screen.getByText('Comparing v1 and v2')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Close compare' }));
  expect(screen.getByTestId('params')).toHaveTextContent('tab=versions');
  expect(screen.getByTestId('params')).not.toHaveTextContent('compare');
  expect(screen.queryByText('Comparing v1 and v2')).not.toBeInTheDocument();
});
it('stops at two ticked versions and does not select the row when a box is ticked', async () => {
  const user = userEvent.setup();
  const onSelectVersion = vi.fn();
  const three = [3, 2, 1].map((number) => ({ ...rows[0], id: `v${number}`, number, labels: [] }));
  render(<ContextVersionsTable {...props} onSelectVersion={onSelectVersion} query={{ data: { results: three, count: 3, pageSize: 100 } }} />);
  await user.click(screen.getByRole('checkbox', { name: 'Select v3 to compare' }));
  await user.click(screen.getByRole('checkbox', { name: 'Select v1 to compare' }));
  expect(screen.getByRole('checkbox', { name: 'Select v2 to compare' })).toBeDisabled();
  expect(onSelectVersion).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Compare v1 and v3' })).toBeVisible();
});
it('preselects the row from the menu Compare item instead of leaving for Tests', async () => {
  const user = userEvent.setup();
  render(<ContextVersionsTable {...props} productionVersion={1} />, '/?tab=versions');
  await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
  await user.click(screen.getByRole('menuitem', { name: 'Compare' }));
  expect(screen.getByRole('checkbox', { name: 'Select v2 to compare' })).toBeChecked();
  expect(screen.getByTestId('params')).toHaveTextContent('tab=versions');
  expect(screen.getByTestId('params')).not.toHaveTextContent('tests');
  await user.click(screen.getByRole('checkbox', { name: 'Select v1 to compare' }));
  expect(screen.getByRole('button', { name: 'Compare v1 and v2' })).toBeVisible();
});
it.each(['1', '2,2', '2,x', '0,1', '1,2,3', '2,1,'])('ignores a malformed compare parameter (%s)', (value) => {
  render(<ContextVersionsTable {...props} />, `/?compare=${value}`);
  expect(screen.queryByRole('region', { name: 'Comparison' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Comparing v/)).not.toBeInTheDocument();
});
it('opens the comparison from the URL in ascending order and keeps both boxes ticked', () => {
  render(<ContextVersionsTable {...props} />, '/?compare=2,1');
  expect(screen.getByText('Comparing v1 and v2')).toBeVisible();
  expect(screen.getByRole('checkbox', { name: 'Select v1 to compare' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Select v2 to compare' })).toBeChecked();
});

it('opens the version from anywhere on the row and keeps View and Compare, in that order, in the trailing ⋯ (UXF-12)', async () => {
  const user = userEvent.setup();
  const onSelectVersion = vi.fn();
  render(<ContextVersionsTable {...props} productionVersion={1} onSelectVersion={onSelectVersion} />);
  const row = screen.getByText('Note 2').closest('tr');
  expect(row).toHaveAttribute('data-row-link', 'true');
  await user.click(within(row).getByText('Author'));
  expect(onSelectVersion).toHaveBeenLastCalledWith(2);
  row.focus();
  await user.keyboard('{Enter}');
  expect(onSelectVersion).toHaveBeenCalledTimes(2);
  // The menu and the release request never open the row.
  await user.click(within(row).getByRole('button', { name: REQUEST }));
  await user.click(within(row).getByRole('button', { name: 'Actions for v2' }));
  expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['View', 'Compare']);
  expect(onSelectVersion).toHaveBeenCalledTimes(2);
  // No outlined button and no lone chevron in any row: the release request is the one inline (ghost) action.
  await user.keyboard('{Escape}');
  expect(screen.getByRole('table', { name: 'Context versions' }).querySelectorAll('tbody .MuiButton-outlined')).toHaveLength(0);
  expect(screen.queryByText('View', { selector: 'tbody button' })).not.toBeInTheDocument();
});
it('names nothing with the retired release words', () => {
  render(<ContextVersionsTable {...props} productionVersion={1} />);
  expect(screen.queryByText(/Request release review|Prepare review/)).not.toBeInTheDocument();
});
