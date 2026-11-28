import PropTypes from 'prop-types';
import { Box } from '@mui/material';
import { formatUsd } from '../utils/format';

// A money figure for people ("$0.24", "under $0.01"); hovering shows the exact reported decimal.
// A missing value reads as `unknown`, never as $0.
export default function Money({ value, round = 'nearest', unknown = 'unknown' }) {
  const text = formatUsd(value, { round });
  if (text === null) return unknown;
  return <Box component="span" title={`Exact: USD ${value}`} sx={{ fontVariantNumeric: 'tabular-nums' }}>{text}</Box>;
}
Money.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
  round: PropTypes.oneOf(['nearest', 'up', 'down']),
  unknown: PropTypes.string,
};
