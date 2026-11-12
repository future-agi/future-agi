import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Box, Button, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import './skillIcons';

export default function TechnicalDetails({ title = 'Details', defaultOpen = false, children }) {
  const id = useId();
  const [open, setOpen] = useState(defaultOpen);
  return <Box sx={{ minWidth: 0, maxWidth: 1 }}>
    <Button id={`${id}-trigger`} size="small" color="inherit" aria-expanded={open} aria-controls={id} onClick={() => setOpen((current) => !current)} startIcon={<Iconify icon={open ? 'solar:alt-arrow-down-linear' : 'solar:alt-arrow-right-linear'} />}>
      <Typography component="span" variant="s3">{title}</Typography>
    </Button>
    <Box id={id} role="region" aria-labelledby={`${id}-trigger`} hidden={!open} sx={{ minWidth: 0, pt: open ? 1 : 0, overflowWrap: 'anywhere', '& pre': { maxWidth: 1, overflowX: 'auto', typography: 's3', fontFamily: 'monospace' } }}>
      {open && children}
    </Box>
  </Box>;
}

TechnicalDetails.propTypes = {
  title: PropTypes.string,
  defaultOpen: PropTypes.bool,
  children: PropTypes.node,
};
