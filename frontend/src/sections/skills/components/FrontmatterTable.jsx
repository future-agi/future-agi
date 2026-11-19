import PropTypes from 'prop-types';
import { Box, Typography } from '@mui/material';

export default function FrontmatterTable({ frontmatter }) {
  if (!frontmatter || !Object.keys(frontmatter).length) return null;
  return (
    <Box component="dl" aria-label="Skill frontmatter" sx={{ m: 0, mb: 2, p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1, display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'minmax(0, 1fr) minmax(0, 3fr)' }, gap: 1 }}>
      {Object.entries(frontmatter).map(([key, value]) => (
        <Box key={key} sx={{ display: 'contents' }}>
          <Typography component="dt" variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>{key}</Typography>
          <Typography component="dd" variant="s1" sx={{ m: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
            {typeof value === 'object' && value !== null ? JSON.stringify(value, null, 2) : String(value ?? '')}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
FrontmatterTable.propTypes = { frontmatter: PropTypes.object };
