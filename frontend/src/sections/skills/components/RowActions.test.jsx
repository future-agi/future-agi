import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PropTypes from 'prop-types';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { Checkbox, Table, TableBody, TableCell, TableRow } from '@mui/material';
import RowActions, { RowActionsCell } from './RowActions';
import { rowLinkProps, stackedTableSx } from './responsiveTable';

const media = (phone) => (query) => ({
  matches: phone && /max-width/.test(query), media: query,
  addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false; },
});

beforeEach(() => vi.stubGlobal('matchMedia', media(false)));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function VersionRow({ primary, actions, inline, withCheckbox = false }) {
  return (
    <MemoryRouter>
      <Table sx={stackedTableSx}>
        <TableBody>
          <TableRow data-testid="row" {...rowLinkProps(primary)}>
            {withCheckbox && <TableCell padding="checkbox"><Checkbox inputProps={{ 'aria-label': 'Select v2' }} /></TableCell>}
            <TableCell>v2</TableCell>
            <TableCell>Published 16 Nov</TableCell>
            <RowActionsCell name="v2" primary={primary} actions={actions} inline={inline} />
          </TableRow>
        </TableBody>
      </Table>
    </MemoryRouter>
  );
}
VersionRow.propTypes = { primary: PropTypes.object, actions: PropTypes.array, inline: PropTypes.oneOfType([PropTypes.string, PropTypes.object]), withCheckbox: PropTypes.bool };

const setup = () => {
  const primary = { label: 'Inspect', onClick: vi.fn() };
  const compare = { label: 'Compare', onClick: vi.fn() };
  const archive = { label: 'Archive', onClick: vi.fn(), danger: true };
  return { primary, compare, archive };
};

describe('RowActions on desktop', () => {
  it('opens the primary target when the row is clicked or Enter is pressed on it', async () => {
    const user = userEvent.setup();
    const { primary, compare } = setup();
    render(<VersionRow primary={primary} actions={[compare]} />);
    const row = screen.getByTestId('row');
    expect(row).toHaveAttribute('data-row-link', 'true');
    await user.click(screen.getByText('Published 16 Nov'));
    expect(primary.onClick).toHaveBeenCalledTimes(1);
    row.focus();
    await user.keyboard('{Enter}');
    expect(primary.onClick).toHaveBeenCalledTimes(2);
    expect(compare.onClick).not.toHaveBeenCalled();
  });

  it('keeps every action, the primary first, in the trailing ⋯ and nowhere else', async () => {
    const user = userEvent.setup();
    const { primary, compare, archive } = setup();
    render(<VersionRow primary={primary} actions={[compare, archive]} />);
    const row = screen.getByTestId('row');
    const buttons = within(row).getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName('Actions for v2');
    expect(buttons[0]).toHaveAttribute('aria-haspopup', 'menu');
    await user.click(buttons[0]);
    expect(primary.onClick).not.toHaveBeenCalled();
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem');
    expect(items.map((item) => item.textContent)).toEqual(['Inspect', 'Compare', 'Archive']);
    await user.click(items[2]);
    expect(archive.onClick).toHaveBeenCalledTimes(1);
    // A menu click bubbles through the portal in React; it must not open the row.
    expect(primary.onClick).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument());
  });

  it('shows a disabled action with its reason and does not run it', async () => {
    const user = userEvent.setup();
    const { primary } = setup();
    const move = { label: 'Move label', onClick: vi.fn(), disabled: true, reason: 'Only workspace admins can move production.' };
    render(<VersionRow primary={primary} actions={[move]} />);
    await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
    const item = screen.getByRole('menuitem', { name: /Move label/ });
    expect(item).toHaveAttribute('aria-disabled', 'true');
    expect(item).toHaveTextContent('Only workspace admins can move production.');
    fireEvent.click(item);
    expect(move.onClick).not.toHaveBeenCalled();
  });

  it('renders the one inline action as a small ghost button that does not open the row', async () => {
    const user = userEvent.setup();
    const { primary } = setup();
    const check = { label: 'Check for update', onClick: vi.fn() };
    render(<VersionRow primary={primary} inline={check} />);
    const inline = screen.getByRole('button', { name: 'Check for update' });
    expect(inline).toHaveClass('MuiButton-text');
    expect(inline).toHaveClass('MuiButton-sizeSmall');
    expect(inline).not.toHaveClass('MuiButton-outlined');
    expect(inline).not.toHaveClass('MuiButton-contained');
    await user.click(inline);
    expect(check.onClick).toHaveBeenCalledTimes(1);
    expect(primary.onClick).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Inspect']);
  });

  it('can show the primary inline (inbox "Open request") and still lists it first in ⋯', async () => {
    const user = userEvent.setup();
    const primary = { label: 'Open request', onClick: vi.fn() };
    render(<VersionRow primary={primary} inline="primary" />);
    const inline = screen.getByRole('button', { name: 'Open request' });
    expect(inline).toHaveClass('MuiButton-text');
    await user.click(inline);
    expect(primary.onClick).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Actions for v2' }));
    expect(within(screen.getByRole('menu')).getAllByRole('menuitem')[0]).toHaveTextContent('Open request');
  });

  it('leaves controls inside the row to themselves', async () => {
    const user = userEvent.setup();
    const { primary } = setup();
    render(<VersionRow primary={primary} withCheckbox />);
    await user.click(screen.getByRole('checkbox', { name: 'Select v2' }));
    expect(primary.onClick).not.toHaveBeenCalled();
  });

  it('gives an ordinary row when the primary target is disabled', async () => {
    const user = userEvent.setup();
    const primary = { label: 'Inspect', onClick: vi.fn(), disabled: true };
    render(<VersionRow primary={primary} />);
    const row = screen.getByTestId('row');
    expect(row).not.toHaveAttribute('data-row-link');
    expect(row).not.toHaveAttribute('tabindex');
    await user.click(screen.getByText('v2'));
    expect(primary.onClick).not.toHaveBeenCalled();
  });
});

describe('RowActions at 390 px', () => {
  beforeEach(() => vi.stubGlobal('matchMedia', media(true)));

  it('makes the card tappable with a trailing chevron and no button', async () => {
    const user = userEvent.setup();
    const { primary, compare } = setup();
    render(<VersionRow primary={primary} actions={[compare]} inline={{ label: 'Check for update', onClick: vi.fn() }} />);
    const row = screen.getByTestId('row');
    await waitFor(() => expect(row.querySelector('[data-row-chevron="true"]')).not.toBeNull());
    expect(within(row).queryAllByRole('button')).toHaveLength(0);
    expect(row.querySelector('[data-cell="row-chevron"]')).not.toBeNull();
    expect(row.querySelector('[data-row-chevron="true"] svg')).not.toBeNull();
    await user.click(screen.getByText('v2'));
    expect(primary.onClick).toHaveBeenCalledTimes(1);
  });

  it('keeps the ⋯ when the row has no primary target, so no action becomes unreachable', async () => {
    const { compare } = setup();
    render(<VersionRow actions={[compare]} />);
    const row = screen.getByTestId('row');
    await waitFor(() => expect(within(row).getByRole('button', { name: 'Actions for v2' })).toBeInTheDocument());
    expect(row.querySelector('[data-row-chevron="true"]')).toBeNull();
    expect(row.querySelector('[data-cell="actions"]')).not.toBeNull();
  });

  it('keeps the ⋯ instead of the chevron when the owner opts in', async () => {
    const { primary, compare } = setup();
    render(<MemoryRouter><RowActions name="v2" primary={primary} actions={[compare]} phoneMenu /></MemoryRouter>);
    expect(await screen.findByRole('button', { name: 'Actions for v2' })).toBeInTheDocument();
    expect(document.querySelector('[data-row-chevron="true"]')).toBeNull();
  });
});
