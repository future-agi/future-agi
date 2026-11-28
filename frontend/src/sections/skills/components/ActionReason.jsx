import PropTypes from 'prop-types';
import { Typography } from '@mui/material';

/**
 * Says why the primary beside it is disabled, in one quiet line, so a dead
 * button never goes unexplained. Place it next to the button (it wraps under
 * the button on phones when the row wraps) and pair the button with
 * `reasonProps(id, reason)`. Renders nothing when there is no reason.
 * Icon-only buttons use a Tooltip instead.
 */
export default function ActionReason({ id, reason, sx }) {
  if (!reason) return null;
  return <Typography id={id} variant="s3" color="text.secondary" sx={{ minWidth: 0, flexBasis: { xs: '100%', sm: 'auto' }, ...sx }}>{reason}</Typography>;
}
ActionReason.propTypes = { id: PropTypes.string.isRequired, reason: PropTypes.node, sx: PropTypes.object };

/** Props for the disabled button, linking it to its reason only when one is shown. */
// eslint-disable-next-line react-refresh/only-export-components -- the pairing helper ships with its component (SH4 contract).
export function reasonProps(id, reason) {
  return reason ? { 'aria-describedby': id } : {};
}
