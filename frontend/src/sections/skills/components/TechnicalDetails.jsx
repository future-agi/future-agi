import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Box, Button, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import './skillIcons';

// Pass open and onToggle to keep the disclosure open across a remount or a swap between two
// renderings of the same section (for example while data loads); otherwise it manages itself.
export default function TechnicalDetails({ title = 'Details', defaultOpen = false, keepMounted = false, open: openProp, onToggle, children }) {
  const id = useId();
  const [ownOpen, setOwnOpen] = useState(defaultOpen);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : ownOpen;
  const toggle = () => (controlled ? onToggle?.(!openProp) : setOwnOpen((current) => !current));
  return <Box sx={{ minWidth: 0, maxWidth: 1 }}>
    <Button id={`${id}-trigger`} size="small" color="inherit" aria-expanded={open} aria-controls={id} onClick={toggle} startIcon={<Iconify icon={open ? 'solar:alt-arrow-down-linear' : 'solar:alt-arrow-right-linear'} />}>
      <Typography component="span" variant="s3">{title}</Typography>
    </Button>
    <Box id={id} role="region" aria-labelledby={`${id}-trigger`} hidden={!open} sx={{ minWidth: 0, pt: open ? 1 : 0, overflowWrap: 'anywhere', '& pre': { maxWidth: 1, overflowX: 'auto', typography: 's3', fontFamily: 'monospace' } }}>
      {(open || keepMounted) && children}
    </Box>
  </Box>;
}

TechnicalDetails.propTypes = {
  title: PropTypes.string,
  defaultOpen: PropTypes.bool,
  keepMounted: PropTypes.bool,
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  children: PropTypes.node,
};
