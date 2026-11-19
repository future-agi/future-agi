import PropTypes from 'prop-types';
import { Stack } from '@mui/material';

// The one order for every decision footer (UX iteration 5, REV1 and REV4).
//   Left:  the acknowledgment checkbox or checkboxes, then tertiary text buttons.
//   Right: the reason (only while the primary waits), the outlined secondary or
//          Cancel, then the contained primary at the far right.
// Below `sm` the checkboxes take the full width, the reason is one line, and the
// two buttons sit side by side at half width each. The caller owns the sticky
// shell (`data-sticky-footer`, useStickyFooterHeight) around this row.
export default function DecisionFooterRow({ left, reason, secondary, primary }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} useFlexGap gap={1.5} flexWrap={{ sm: 'wrap' }} alignItems={{ xs: 'stretch', sm: 'center' }}>
      <Stack direction="row" useFlexGap columnGap={1} rowGap={0.5} flexWrap="wrap" alignItems="center" data-decision-left sx={{ flex: { sm: '1 1 280px' }, minWidth: 0 }}>
        {left}
      </Stack>
      <Stack direction="row" useFlexGap gap={1} flexWrap="wrap" alignItems="center" data-decision-right
        sx={{ minWidth: 0, ml: { sm: 'auto' }, justifyContent: { sm: 'flex-end' }, '& > .MuiButton-root': { flex: { xs: '1 1 0', sm: '0 0 auto' }, minWidth: 0 } }}>
        {reason}
        {secondary}
        {primary}
      </Stack>
    </Stack>
  );
}
DecisionFooterRow.propTypes = { left: PropTypes.node, reason: PropTypes.node, secondary: PropTypes.node, primary: PropTypes.node };
