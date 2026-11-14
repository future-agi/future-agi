import { useState } from 'react';
import PropTypes from 'prop-types';
import { Box, IconButton, Tooltip, Typography } from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import Iconify from 'src/components/iconify';
import { shortDigest } from '../utils/format';
import './skillIcons';

// Presentation only: never use the shortened text for comparisons or payloads.
// For a version, prefer VersionLabel ("v2", full digest on hover or Enter):
// since UX iteration 3 (SH3) no digest is primary content in headers or
// tables. Keep this chip for records whose short form is what people need,
// inside TechnicalDetails or other secondary places.
export default function IdentityChip({ value, label, kind = 'digest', length = 12, inline = false }) {
  const [failedValue, setFailedValue] = useState(null);
  const present = typeof value === 'string' && value.length > 0;
  const shortened = present ? (kind === 'id' ? value.replace(/^(?:release|context|consumer|runtime|session|binding)-/, '').slice(0, 8) : shortDigest(value).slice(0, length)) : '';
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setFailedValue(null);
      enqueueSnackbar('Copied');
    } catch {
      setFailedValue(value);
    }
  };
  return <Box component="span" sx={{ display: inline ? 'inline-flex' : 'flex', flexDirection: 'column', minWidth: 0, maxWidth: 1, verticalAlign: 'middle' }}>
    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, maxWidth: 1, minWidth: 0 }}>
      {label && <Typography component="span" variant="s3" color="text.secondary">{label}</Typography>}
      {present ? <>
        <Tooltip title={value} describeChild>
          <Typography component="code" variant="s3" tabIndex={0} sx={{ fontFamily: 'monospace', whiteSpace: 'nowrap', flexShrink: 0 }}>{shortened}…</Typography>
        </Tooltip>
        <IconButton size="small" aria-label={`Copy full ${label || 'identifier'}`} onClick={copy}><Iconify icon="solar:copy-linear" /></IconButton>
      </> : <Typography component="span" variant="s3" color="text.secondary">Not reported</Typography>}
    </Box>
    {present && failedValue === value && <Box component="span" role="status" sx={{ minWidth: 0 }}>
      <Typography component="span" variant="s3" display="block">Couldn&apos;t copy. Select the full value in Details.</Typography>
      <Typography component="code" variant="s3" tabIndex={0} sx={{ display: 'block', fontFamily: 'monospace', overflowWrap: 'anywhere', userSelect: 'all' }}>{value}</Typography>
    </Box>}
  </Box>;
}

IdentityChip.propTypes = {
  value: PropTypes.string,
  label: PropTypes.string,
  kind: PropTypes.oneOf(['digest', 'id']),
  length: PropTypes.number,
  inline: PropTypes.bool,
};
