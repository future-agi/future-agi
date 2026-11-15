import { cleanup, render as rtlRender, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import ContextVersionsTable from './ContextVersionsTable';
vi.mock('./ContextLabelRequestPanel', () => ({ default: ({ version }) => <button type="button" data-version={version.number}>Request release review</button> }));
afterEach(cleanup);
function Params() { const [params] = useSearchParams(); return <output data-testid="params">{params.toString()}</output>; }
const render = (ui) => rtlRender(<MemoryRouter>{ui}<Params /></MemoryRouter>);
const rows = [2, 1].map((number) => ({ id: `v${number}`, number, note: `Note ${number}`, createdBy: { name: 'Author' }, fileCount: number + 2, labels: number === 1 ? ['production'] : [] }));
const props = { contextId: 'context', latestVersion: 2, query: { data: { results: rows, count: 2, pageSize: 100 } }, page: 1, onPageChange: vi.fn(), onSelectVersion: vi.fn() };
it('shows version metadata and requests review only above production', () => {
  render(<ContextVersionsTable {...props} productionVersion={1} />);
  expect(screen.getByRole('table', { name: 'Context versions' })).toBeVisible();
  expect(screen.getAllByRole('button', { name: 'Request release review' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Request release review' })).toHaveAttribute('data-version', '2');
  expect(screen.getByText('Production')).toBeVisible();
  expect(screen.queryByText('v1 is in production.')).not.toBeInTheDocument();
  expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent).filter(Boolean)).toEqual(['Version', 'Note', 'Author', 'Published', 'Labels', 'Files', 'Release', 'Actions']);
  const requestCell = screen.getByRole('button', { name: 'Request release review' }).closest('td');
  expect(requestCell.cellIndex).toBe(6);
  expect(screen.getByRole('combobox', { name: 'Rows per page' })).toBeVisible();
});
it('offers exactly one latest candidate before first production, not every historical version', () => {
  render(<ContextVersionsTable {...props} />);
  expect(screen.getAllByRole('button', { name: 'Request release review' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Request release review' })).toHaveAttribute('data-version', '2');
});
it('does not offer a forward release from production to itself or an older version', () => {
  render(<ContextVersionsTable {...props} productionVersion={2} />);
  expect(screen.queryByRole('button', { name: 'Request release review' })).not.toBeInTheDocument();
  expect(screen.queryByText('v2 is in production.')).not.toBeInTheDocument();
});

it('keeps review scoped to the viewed candidate when several publications are above production', () => {
  render(<ContextVersionsTable {...props} latestVersion={3} viewedNumber={2} productionVersion={1} query={{ data: { ...props.query.data, count: 3, results: [{ ...rows[0], id: 'v3', number: 3 }, ...rows] } }} />);
  expect(screen.getAllByRole('button', { name: 'Request release review' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Request release review' })).toHaveAttribute('data-version', '2');
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
  await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
  await user.click(screen.getByRole('menuitem', { name: 'Compare' }));
  expect(screen.getByTestId('params')).toHaveTextContent('version=2&tab=tests');
});
