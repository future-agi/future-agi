import PropTypes from 'prop-types';
import { Stack, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import TechnicalDetails from './TechnicalDetails';

export default function CapabilityNotice({ title, description, actions, details }) {
  return <Stack direction="row" spacing={1.5} sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1, minWidth: 0 }}>
    <Iconify icon="solar:info-circle-linear" sx={{ color: 'text.secondary', flexShrink: 0, mt: 0.25 }} />
    <Stack spacing={1} sx={{ minWidth: 0, flex: 1 }}>
      <Typography component="h2" variant="m3">{title}</Typography>
      <Typography variant="s1" color="text.secondary">{description}</Typography>
      {actions && <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>{actions}</Stack>}
      {details && <TechnicalDetails>{details}</TechnicalDetails>}
    </Stack>
  </Stack>;
}

CapabilityNotice.propTypes = {
  title: PropTypes.string.isRequired,
  description: PropTypes.node.isRequired,
  actions: PropTypes.node,
  details: PropTypes.node,
};
