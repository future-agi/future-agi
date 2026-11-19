import { useState } from 'react';
import PropTypes from 'prop-types';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import TestsSubTabs, { DEFAULT_TESTS_SUB_TAB, TESTS_SUB_TABS } from './TestsSubTabs';

afterEach(cleanup);

function Harness({ initial }) {
  const [value, setValue] = useState(initial);
  return <TestsSubTabs value={value} onChange={setValue} label="Example tests" results={<p>Latest result</p>} run={<input aria-label="Request" />} cases={<p>Saved cases</p>} />;
}
Harness.propTypes = { initial: PropTypes.string.isRequired };

it('lists Results, Run a test and Cases in that order, with Results by default', () => {
  expect(TESTS_SUB_TABS.map(([, label]) => label)).toEqual(['Results', 'Run a test', 'Cases']);
  expect(DEFAULT_TESTS_SUB_TAB).toBe('results');
  render(<TestsSubTabs results={<p>Latest result</p>} run={<p>Setup</p>} cases={<p>Saved cases</p>} />);
  expect(screen.getByRole('tablist', { name: 'Tests views' })).toBeInTheDocument();
  expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual(['Results', 'Run a test', 'Cases']);
  expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Latest result');
});
it('falls back to Results for an unknown value', () => {
  render(<TestsSubTabs value="history" results={<p>Latest result</p>} />);
  expect(screen.getByRole('tab', { name: 'Results' })).toHaveAttribute('aria-selected', 'true');
});
it('keeps every panel mounted while another tab is open, so a half-filled setup survives', () => {
  render(<Harness initial="run" />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Request' }), { target: { value: 'Refund order 7' } });
  fireEvent.click(screen.getByRole('tab', { name: 'Cases' }));
  expect(screen.getByRole('tabpanel')).toHaveTextContent('Saved cases');
  expect(screen.queryByRole('textbox', { name: 'Request' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: 'Run a test' }));
  expect(screen.getByRole('textbox', { name: 'Request' })).toHaveValue('Refund order 7');
});
it('links each tab to its panel', () => {
  render(<Harness initial="results" />);
  for (const tab of screen.getAllByRole('tab')) {
    const panel = document.getElementById(tab.getAttribute('aria-controls'));
    expect(panel).toHaveAttribute('role', 'tabpanel');
    expect(panel).toHaveAttribute('aria-labelledby', tab.id);
  }
});
