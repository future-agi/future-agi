import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Box, ButtonBase, IconButton, Popover, Stack, Tooltip, Typography } from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import Iconify from 'src/components/iconify';
import './skillIcons';

// A version reads "v2"; its full digest is one hover, keystroke or click away
// (UX iteration 3, SH3, refining §2.3). Hover shows the full value in a
// tooltip. Click or Enter opens a small popover with the selectable value and
// a keyboard-reachable "Copy full digest" button; Esc returns focus to the
// label. Presentation only: comparisons and payloads keep using the complete
// value they already hold, never this text.
//
// `compact` renders only an info trigger, for a row whose version number is
// already shown by a picker.
export default function VersionLabel({ number, digest, label = 'version', suffix, compact = false, sx }) {
  const [anchor, setAnchor] = useState(null);
  const [failed, setFailed] = useState(false);
  const titleId = useId();
  const present = typeof digest === 'string' && digest.length > 0;
  const text = number != null ? `v${number}` : 'Not reported';
  const visible = suffix ? `${text} ${suffix}` : text;
  if (!present) {
    if (compact) return null;
    return <Typography component="span" variant="s3" sx={{ fontWeight: 600, color: 'text.primary', ...sx }}>{visible}</Typography>;
  }
  const name = `${number != null ? `v${number}` : 'This version'}, show full ${label} digest`;
  const open = Boolean(anchor);
  const close = () => { setAnchor(null); setFailed(false); };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(digest);
      setFailed(false);
      enqueueSnackbar('Copied');
    } catch {
      setFailed(true);
    }
  };
  const trigger = compact
    ? <IconButton size="small" aria-label={name} aria-haspopup="dialog" aria-expanded={open} onClick={(event) => setAnchor(event.currentTarget)} sx={{ color: 'text.secondary', ...sx }}>
      <Iconify icon="solar:info-circle-linear" width={16} />
    </IconButton>
    : <ButtonBase
      aria-label={name} aria-haspopup="dialog" aria-expanded={open}
      onClick={(event) => setAnchor(event.currentTarget)}
      sx={{
        typography: 's3', fontWeight: 600, color: 'text.primary', borderRadius: 0.5, px: 0.25, mx: -0.25,
        textDecoration: 'underline', textDecorationStyle: 'dotted', textDecorationColor: (theme) => theme.palette.text.disabled, textUnderlineOffset: 3,
        '&:hover': { textDecorationColor: (theme) => theme.palette.text.secondary },
        '&.Mui-focusVisible': { outline: 2, outlineColor: 'primary.main', outlineOffset: 1 },
        ...sx,
      }}
    >{visible}</ButtonBase>;
  return <>
    <Tooltip title={open ? '' : digest} describeChild slotProps={{ tooltip: { sx: { maxWidth: 'none', fontFamily: 'monospace' } } }}>{trigger}</Tooltip>
    <Popover
      open={open} anchorEl={anchor} onClose={close}
      anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }} transformOrigin={{ vertical: 'top', horizontal: 'left' }}
      slotProps={{ paper: { role: 'dialog', 'aria-labelledby': titleId, sx: { mt: 0.5, p: 1.5, width: 360, maxWidth: 'calc(100vw - 32px)' } } }}
    >
      <Stack spacing={1}>
        <Typography id={titleId} variant="s3" color="text.secondary">Full {label} digest{number != null ? ` of v${number}` : ''}</Typography>
        <Stack direction="row" spacing={0.5} alignItems="flex-start">
          <Typography component="code" variant="s3" tabIndex={0} sx={{ flex: 1, minWidth: 0, fontFamily: 'monospace', overflowWrap: 'anywhere', userSelect: 'all' }}>{digest}</Typography>
          {/* Focus lands here when the popover opens, so Enter copies at once. */}
          <IconButton size="small" autoFocus aria-label="Copy full digest" onClick={copy}><Iconify icon="solar:copy-linear" width={16} /></IconButton>
        </Stack>
        {failed && <Box role="status"><Typography variant="s3">Couldn&apos;t copy. Select the full value above.</Typography></Box>}
      </Stack>
    </Popover>
  </>;
}

VersionLabel.propTypes = {
  number: PropTypes.number,
  digest: PropTypes.string,
  label: PropTypes.string,
  suffix: PropTypes.string,
  compact: PropTypes.bool,
  sx: PropTypes.object,
};
