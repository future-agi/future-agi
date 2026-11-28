import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import EvidenceSummary from './EvidenceSummary';

// UXF-12: with rowActions the whole row opens Inspect and the action sits in ⋯;
// without it the row keeps its Inspect button (other owners opt in).
// UXF-17: a private row can be ticked only when the caller opts in (it then
// opens the share consent), and carries a neutral "Private" tag.

afterEach(cleanup);

const item = (overrides = {}) => ({ id: 'run', source: 'skills_text', title: 'Answer check · v2 vs v1', status: 'completed', outcome: 'works_on_examples',
  visibility: 'workspace', expiresAt: '2999-01-01', counts: { graded: 2, planned: 2, regressed: 0 }, ...overrides });

describe('EvidenceSummary', () => {
  it('opens Inspect from the whole row, with Inspect in the ⋯ menu and no row button', () => {
    const onInspect = vi.fn();
    render(<EvidenceSummary rowActions items={[item()]} onInspect={onInspect} />);
    expect(screen.queryByRole('button', { name: 'Inspect' })).not.toBeInTheDocument();
    const row = screen.getByText('Answer check · v2 vs v1').closest('tr');
    expect(row).toHaveAttribute('data-row-link', 'true');
    fireEvent.click(row);
    expect(onInspect).toHaveBeenCalledWith(expect.objectContaining({ id: 'run' }));
    fireEvent.click(screen.getByRole('button', { name: 'Actions for Answer check · v2 vs v1' }));
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitem', { name: 'Inspect' }));
    expect(onInspect).toHaveBeenCalledTimes(2);
  });

  it('keeps the Inspect button for callers that have not opted in', () => {
    const onInspect = vi.fn();
    render(<EvidenceSummary items={[item()]} onInspect={onInspect} />);
    fireEvent.click(screen.getByRole('button', { name: 'Inspect' }));
    expect(onInspect).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Answer check · v2 vs v1').closest('tr')).not.toHaveAttribute('data-row-link');
  });

  it('a ticked checkbox does not open the row', () => {
    const onInspect = vi.fn();
    const onToggle = vi.fn();
    render(<EvidenceSummary rowActions selectable items={[item()]} onInspect={onInspect} onToggle={onToggle} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Attach Answer check · v2 vs v1' }));
    expect(onToggle).toHaveBeenCalledTimes(1);
    expect(onInspect).not.toHaveBeenCalled();
  });

  it('lets a private row be ticked only when the caller opts in, with a neutral Private tag', () => {
    const onToggle = vi.fn();
    const view = render(<EvidenceSummary selectable items={[item({ visibility: 'private' })]} onToggle={onToggle} />);
    expect(screen.getByRole('checkbox', { name: 'Attach Answer check · v2 vs v1' })).toBeDisabled();
    view.rerender(<EvidenceSummary selectable privateSelectable items={[item({ visibility: 'private' })]} onToggle={onToggle} />);
    const box = screen.getByRole('checkbox', { name: 'Attach Answer check · v2 vs v1' });
    expect(box).toBeEnabled();
    fireEvent.click(box);
    expect(onToggle).toHaveBeenCalledWith(expect.objectContaining({ visibility: 'private' }));
    expect(screen.getByText('Private')).toBeVisible();
  });
});
