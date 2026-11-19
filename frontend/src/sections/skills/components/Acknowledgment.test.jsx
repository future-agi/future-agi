import { afterEach, describe, expect, it } from 'vitest';
import PropTypes from 'prop-types';
import { act, cleanup, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import Acknowledgment, { acknowledgedInSession, resetSessionAcknowledgments, useAcknowledgment } from './Acknowledgment';

afterEach(() => { cleanup(); resetSessionAcknowledgments(); });

const LABEL = "I've checked the 2 changed files";

function Harness({ identity, disabled, scope, actorId, onAck }) {
  const ack = useAcknowledgment(identity, { scope, actorId });
  onAck?.(ack);
  return <Acknowledgment acknowledgment={ack} label={LABEL} disabled={disabled} />;
}
Harness.propTypes = { identity: PropTypes.string, disabled: PropTypes.bool, scope: PropTypes.string, actorId: PropTypes.string, onAck: PropTypes.func };

describe('Acknowledgment', () => {
  it('ticks through its label and reports the identity it was ticked for', async () => {
    const user = userEvent.setup();
    let latest;
    render(<Harness identity="fp-1" onAck={(ack) => { latest = ack; }} />);
    const box = screen.getByRole('checkbox', { name: LABEL });
    expect(box).not.toBeChecked();
    await user.click(screen.getByText(LABEL));
    expect(box).toBeChecked();
    expect(latest.acknowledged).toBe(true);
    expect(latest.acknowledgedIdentity).toBe('fp-1');
  });

  it('keeps a ticked label at full contrast when the form locks', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness identity="fp-1" />);
    await user.click(screen.getByRole('checkbox', { name: LABEL }));
    rerender(<Harness identity="fp-1" disabled />);
    expect(screen.getByRole('checkbox', { name: LABEL })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: LABEL })).toBeChecked();
    const label = screen.getByText(LABEL);
    expect(label).toHaveAttribute('data-acknowledged', 'true');
    expect(label).not.toHaveClass('Mui-disabled');
  });

  it('resets when the bound identity changes and says why', async () => {
    const user = userEvent.setup();
    let latest;
    const { rerender } = render(<Harness identity="fp-1" onAck={(ack) => { latest = ack; }} />);
    await user.click(screen.getByRole('checkbox', { name: LABEL }));
    rerender(<Harness identity="fp-2" onAck={(ack) => { latest = ack; }} />);
    const box = screen.getByRole('checkbox', { name: LABEL });
    expect(box).not.toBeChecked();
    expect(latest.acknowledgedIdentity).toBeNull();
    expect(screen.getByText('This changed after you ticked it. Check it again.')).toBeInTheDocument();
    expect(box).toHaveAccessibleDescription('This changed after you ticked it. Check it again.');
    await user.click(box);
    expect(latest.acknowledgedIdentity).toBe('fp-2');
    expect(screen.queryByText('This changed after you ticked it. Check it again.')).not.toBeInTheDocument();
  });

  it('asks again after a change back to the earlier identity (A → B → A)', async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness identity="A" />);
    await user.click(screen.getByRole('checkbox', { name: LABEL }));
    rerender(<Harness identity="B" />);
    rerender(<Harness identity="A" />);
    expect(screen.getByRole('checkbox', { name: LABEL })).not.toBeChecked();
  });

  it('cannot be ticked without an identity', async () => {
    const user = userEvent.setup();
    let latest;
    render(<Harness identity={null} onAck={(ack) => { latest = ack; }} />);
    const box = screen.getByRole('checkbox', { name: LABEL });
    expect(box).toBeDisabled();
    act(() => latest.setAcknowledged(true));
    expect(latest.acknowledged).toBe(false);
    await user.click(screen.getByText(LABEL));
    expect(box).not.toBeChecked();
  });
});

describe('useAcknowledgment session memory (UXF-02 final confirmation)', () => {
  const scope = 'release:req-1';

  it('remembers one tick per scope, actor and exact identity', () => {
    const first = renderHook(({ identity }) => useAcknowledgment(identity, { scope, actorId: 'u-1' }), { initialProps: { identity: 'ack-1' } });
    act(() => first.result.current.setAcknowledged(true));
    expect(acknowledgedInSession({ scope, actorId: 'u-1', identity: 'ack-1' })).toBe(true);
    expect(acknowledgedInSession({ scope, actorId: 'u-2', identity: 'ack-1' })).toBe(false);
    expect(acknowledgedInSession({ scope, actorId: 'u-1', identity: 'ack-2' })).toBe(false);
    expect(acknowledgedInSession({ scope: 'release:req-2', actorId: 'u-1', identity: 'ack-1' })).toBe(false);
    first.unmount();
    const confirm = renderHook(() => useAcknowledgment('ack-1', { scope, actorId: 'u-1', restore: true }));
    expect(confirm.result.current.acknowledged).toBe(true);
    expect(confirm.result.current.acknowledgedIdentity).toBe('ack-1');
    const other = renderHook(() => useAcknowledgment('ack-1', { scope, actorId: 'u-2', restore: true }));
    expect(other.result.current.acknowledged).toBe(false);
    const changed = renderHook(() => useAcknowledgment('ack-2', { scope, actorId: 'u-1', restore: true }));
    expect(changed.result.current.acknowledged).toBe(false);
  });

  it('asks again on a fresh mount unless the caller restores (only the final confirmation does)', () => {
    const first = renderHook(() => useAcknowledgment('ack-1', { scope, actorId: 'u-1' }));
    act(() => first.result.current.setAcknowledged(true));
    first.unmount();
    const decision = renderHook(() => useAcknowledgment('ack-1', { scope, actorId: 'u-1' }));
    expect(decision.result.current.acknowledged).toBe(false);
    expect(acknowledgedInSession({ scope, actorId: 'u-1', identity: 'ack-1' })).toBe(true);
  });

  it('forgets the tick on untick and on any identity change, so A → B → A asks again', () => {
    const hook = renderHook(({ identity }) => useAcknowledgment(identity, { scope, actorId: 'u-1' }), { initialProps: { identity: 'A' } });
    act(() => hook.result.current.setAcknowledged(true));
    act(() => hook.result.current.setAcknowledged(false));
    expect(acknowledgedInSession({ scope, actorId: 'u-1', identity: 'A' })).toBe(false);
    act(() => hook.result.current.setAcknowledged(true));
    hook.rerender({ identity: 'B' });
    hook.rerender({ identity: 'A' });
    expect(hook.result.current.acknowledged).toBe(false);
    expect(acknowledgedInSession({ scope, actorId: 'u-1', identity: 'A' })).toBe(false);
  });

  it('remembers nothing without an actor', () => {
    const hook = renderHook(() => useAcknowledgment('A', { scope }));
    act(() => hook.result.current.setAcknowledged(true));
    expect(hook.result.current.acknowledged).toBe(true);
    expect(acknowledgedInSession({ scope, identity: 'A' })).toBe(false);
    hook.unmount();
    expect(renderHook(() => useAcknowledgment('A', { scope, restore: true })).result.current.acknowledged).toBe(false);
  });
});
