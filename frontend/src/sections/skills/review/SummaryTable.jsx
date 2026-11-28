import PropTypes from 'prop-types';
import { Box, Typography } from '@mui/material';

// A bordered key/value table (Vel's "How it was decided"): fixed label column,
// hairline rows, no card around it. Below sm the label stacks over the value.
// Rows with no value are skipped so the table never shows an empty line.
export default function SummaryTable({ rows, labelWidth = 160, label }) {
  const shown = rows.filter((row) => row && row.value != null && row.value !== false);
  if (!shown.length) return null;
  return <Box component="dl" aria-label={label} sx={{ m: 0, minWidth: 0, border: 1, borderColor: 'divider', borderRadius: '6px', overflow: 'hidden' }}>
    {shown.map((row) => <Box key={row.label} sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: `${labelWidth}px minmax(0, 1fr)` }, columnGap: 1.5, rowGap: 0.25, alignItems: 'baseline', px: 1.5, py: 1, borderTop: 1, borderColor: 'divider', '&:first-of-type': { borderTop: 0 } }}>
      <Typography component="dt" variant="s3" color="text.secondary">{row.label}</Typography>
      <Typography component="dd" variant="s1" sx={{ m: 0, minWidth: 0, overflowWrap: 'anywhere' }}>{row.value}</Typography>
    </Box>)}
  </Box>;
}
SummaryTable.propTypes = {
  rows: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string.isRequired, value: PropTypes.node })).isRequired,
  labelWidth: PropTypes.number,
  label: PropTypes.string,
};
