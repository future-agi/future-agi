import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ReleaseRuleNote from './ReleaseRuleNote';

afterEach(cleanup);

describe('ReleaseRuleNote (UXF-09)', () => {
  it('builds the text from the given rules, with no label name of its own', () => {
    const { rerender } = render(<ReleaseRuleNote rules={{ label: 'gold', protectedLabels: ['gold'] }} />);
    const note = screen.getByTestId('release-rule-note');
    expect(note).toHaveTextContent("Gold is a protected label: only workspace admins can move it, and automation can't.");
    expect(note.textContent.toLowerCase()).not.toContain('production');
    rerender(<ReleaseRuleNote rules={{ label: 'production', protectedLabels: ['gold'] }} />);
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent("Production isn't a protected label: anyone who can edit can move it.");
  });

  it('follows a change to the workspace rules on the next render', () => {
    const { rerender } = render(<ReleaseRuleNote rules={{ protectedLabels: ['gold'] }} />);
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent('Gold is a protected label');
    rerender(<ReleaseRuleNote rules={{ protectedLabels: ['gold', 'silver'] }} />);
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent("Protected labels (only workspace admins can move them, and automation can't): gold, silver.");
  });

  it('explains the second-person review only when the rules require it', () => {
    const { rerender } = render(<ReleaseRuleNote rules={{ reviewRequired: true, subjectKind: 'agent_context' }} />);
    expect(screen.getByTestId('release-rule-note')).toHaveTextContent("Agent context releases need a second person's review, because one release can change several skills.");
    rerender(<ReleaseRuleNote rules={{ reviewRequired: false }} />);
    expect(screen.queryByTestId('release-rule-note')).not.toBeInTheDocument();
  });

  it('renders nothing when no rule is given', () => {
    render(<ReleaseRuleNote rules={{}} />);
    expect(screen.queryByTestId('release-rule-note')).not.toBeInTheDocument();
  });

  it('offers "Change rules" only to people who can change them', async () => {
    const user = userEvent.setup();
    const onChangeRules = vi.fn();
    const { rerender } = render(<ReleaseRuleNote rules={{ label: 'gold', protectedLabels: ['gold'] }} onChangeRules={onChangeRules} />);
    expect(screen.queryByRole('button', { name: 'Change rules' })).not.toBeInTheDocument();
    rerender(<ReleaseRuleNote rules={{ label: 'gold', protectedLabels: ['gold'] }} canChangeRules onChangeRules={onChangeRules} />);
    const button = screen.getByRole('button', { name: 'Change rules' });
    expect(button).toHaveClass('MuiButton-text');
    await user.click(button);
    expect(onChangeRules).toHaveBeenCalledTimes(1);
  });
});
