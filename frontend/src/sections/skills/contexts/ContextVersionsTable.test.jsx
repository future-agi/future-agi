import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import ContextVersionsTable from './ContextVersionsTable';
vi.mock('./ContextLabelRequestPanel', () => ({ default: ({ version }) => <button type="button" data-version={version.number}>Request release review</button> }));
afterEach(cleanup);
const rows = [2, 1].map((number) => ({ id: `v${number}`, number, note: `Note ${number}`, createdBy: { name: 'Author' }, labels: number === 1 ? ['production'] : [] }));
const props = { contextId: 'context', latestVersion: 2, query: { data: { results: rows, count: 2, pageSize: 100 } }, page: 1, onPageChange: vi.fn(), onSelectVersion: vi.fn() };
it('shows version metadata and requests review only above production', () => {
  render(<ContextVersionsTable {...props} productionVersion={1} />);
  expect(screen.getByRole('table', { name: 'Context versions' })).toBeVisible();
  expect(screen.getAllByRole('button', { name: 'Request release review' })).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'Request release review' })).toHaveAttribute('data-version', '2');
  expect(screen.getByText('Production')).toBeVisible();
  expect(screen.queryByText('v1 is in production.')).not.toBeInTheDocument();
  expect(screen.getAllByRole('columnheader').map((cell) => cell.textContent)).toEqual(['Version', 'Note', 'Author', 'Published', 'Labels', 'Actions']);
  const requestCell = screen.getByRole('button', { name: 'Request release review' }).closest('td');
  expect(requestCell.cellIndex).toBe(5);
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
