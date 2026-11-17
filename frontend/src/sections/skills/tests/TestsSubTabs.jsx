import { useId } from 'react';
import PropTypes from 'prop-types';
import { Box, Tab } from '@mui/material';
import { CustomTabs } from 'src/components/tabs/tabs';

// The one layout for every Tests tab, skill and agent context alike (SKILL-TRACES §16 UXF-04):
// Results (the latest result, then History), Run a test (the setup), Cases (the test cases).
// The order never changes with state, and Results is the default.
//
// Every panel stays mounted while hidden. The run setups own their operation keys, previews and
// ticks (an uncertain paid submission is retried with the same key), so switching tabs must never
// drop them.
// eslint-disable-next-line react-refresh/only-export-components -- the fixed order ships with its component.
export const TESTS_SUB_TABS = Object.freeze([
  ['results', 'Results'],
  ['run', 'Run a test'],
  ['cases', 'Cases'],
]);
// eslint-disable-next-line react-refresh/only-export-components -- the default ships with its component.
export const DEFAULT_TESTS_SUB_TAB = 'results';

export default function TestsSubTabs({ value = DEFAULT_TESTS_SUB_TAB, onChange, label = 'Tests views', results, run, cases }) {
  const base = useId().replace(/:/g, '');
  const panels = { results, run, cases };
  const current = TESTS_SUB_TABS.some(([key]) => key === value) ? value : DEFAULT_TESTS_SUB_TAB;
  return (
    <Box sx={{ minWidth: 0 }}>
      <CustomTabs value={current} onChange={(_, next) => onChange?.(next)} aria-label={label}>
        {TESTS_SUB_TABS.map(([key, text]) => (
          <Tab key={key} value={key} label={text} id={`${base}-${key}-tab`} aria-controls={`${base}-${key}-panel`} />
        ))}
      </CustomTabs>
      {TESTS_SUB_TABS.map(([key]) => (
        <Box key={key} role="tabpanel" id={`${base}-${key}-panel`} aria-labelledby={`${base}-${key}-tab`} hidden={current !== key}
          data-tests-panel={key} sx={{ pt: 2, minWidth: 0 }}>
          {panels[key]}
        </Box>
      ))}
    </Box>
  );
}

TestsSubTabs.propTypes = {
  /** 'results' | 'run' | 'cases'. Defaults to Results. */
  value: PropTypes.oneOf(TESTS_SUB_TABS.map(([key]) => key)),
  onChange: PropTypes.func,
  /** Accessible name of the tab strip. */
  label: PropTypes.string,
  results: PropTypes.node,
  run: PropTypes.node,
  cases: PropTypes.node,
};
