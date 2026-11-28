import PropTypes from 'prop-types';
import { Box, Tooltip } from '@mui/material';
import { formatExpiry, formatExpiryShort, formatUtcFull, formatWhen } from '../utils/format';

/**
 * One way to show a time in Skills: short and relative in the view, the full
 * UTC time in a tooltip that keyboard users reach by focusing the text.
 * `kind="expiry"` reads "Expires in 7 days"; `kind="expiry-short"` reads
 * "in 7 days" for a cell under an "Expires" header.
 */
export default function When({ value, kind = 'when', now, fallback = 'Not reported', sx }) {
  const full = formatUtcFull(value);
  if (!full) return <Box component="span" sx={sx}>{fallback}</Box>;
  const options = now == null ? undefined : { now };
  const text = kind === 'expiry' ? formatExpiry(value, options) : kind === 'expiry-short' ? formatExpiryShort(value, options) : formatWhen(value, options);
  return <Tooltip title={full} describeChild>
    <Box component="time" dateTime={new Date(value).toISOString()} tabIndex={0} sx={{ whiteSpace: 'nowrap', borderRadius: 0.5, '&:focus-visible': { outline: 2, outlineColor: 'primary.main', outlineOffset: 2 }, ...sx }}>{text}</Box>
  </Tooltip>;
}
When.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.instanceOf(Date)]),
  kind: PropTypes.oneOf(['when', 'expiry', 'expiry-short']),
  now: PropTypes.number,
  fallback: PropTypes.node,
  sx: PropTypes.object,
};
