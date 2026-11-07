import PropTypes from 'prop-types';
import { Chip } from '@mui/material';

export default function LabelChip({ name, version }) {
  return <Chip size="small" variant="outlined" label={version == null ? name : `${name} · v${version}`} />;
}
LabelChip.propTypes = { name: PropTypes.string.isRequired, version: PropTypes.number };
