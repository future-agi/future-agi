import PropTypes from 'prop-types';
import { Button, Stack } from '@mui/material';
import { LoadingButton } from '@mui/lab';
import ActionReason, { reasonProps } from '../components/ActionReason';

// One footer for every Import skills tab (UX iteration 3, LIB2). The reason a primary is
// unavailable sits on the left, then Cancel, then the primary on the far right, so the
// primary never moves when the person switches tabs. Each tab supplies only its body plus
// { label, enabled, reason, onPrimary, busy }.
// Intent: finish or leave the import. Hierarchy: Preview import is the only contained action.
// Palette/depth/surfaces: flat row, no divider of its own. Typography/spacing: s3 reason, 8px gap.
export default function ImportFooter({ label = 'Preview import', enabled = false, reason = '', onPrimary, onCancel, busy = false, hidePrimary = false }) {
  return <Stack direction="row" alignItems="center" justifyContent="flex-end" useFlexGap flexWrap="wrap" gap={1} sx={{ mt: 2, flexShrink: 0, bgcolor: 'background.paper' }}>
    <ActionReason id="import-action-reason-1" reason={reason} sx={{ flex: 1, flexBasis: { xs: '100%', sm: 0 }, textAlign: { sm: 'left' } }} />
    {!reason && <span style={{ flex: 1 }} />}
    <Button size="small" variant="outlined" disabled={busy} onClick={onCancel} sx={{ minWidth: 90 }}>Cancel</Button>
    {!hidePrimary && <LoadingButton size="small" variant="contained" color="primary" type="submit" loading={busy} disabled={!enabled || busy} onClick={onPrimary} sx={{ minWidth: 90 }} {...reasonProps('import-action-reason-1', reason)}>{label}</LoadingButton>}
  </Stack>;
}
ImportFooter.propTypes = {
  label: PropTypes.string,
  enabled: PropTypes.bool,
  reason: PropTypes.node,
  onPrimary: PropTypes.func,
  onCancel: PropTypes.func.isRequired,
  busy: PropTypes.bool,
  hidePrimary: PropTypes.bool,
};
