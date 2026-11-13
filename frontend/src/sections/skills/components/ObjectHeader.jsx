import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Breadcrumbs, Link, Stack, Typography } from '@mui/material';
import PageHeader from 'src/components/page-header';

export default function ObjectHeader({ crumbs = [], title, purpose, badge, identity, actions, tabs, sx }) {
  // Flex gap keeps the compact header rhythm when mobile breadcrumbs are hidden.
  return <Stack useFlexGap spacing={{ xs: 1, sm: 1.5 }} sx={{ minWidth: 0, ...sx }}>
    {crumbs.length > 0 && <Breadcrumbs aria-label="Breadcrumb" sx={{ typography: 's3', overflowWrap: 'anywhere' }}>
      {crumbs.map((crumb, index) => index < crumbs.length - 1 && crumb.to
        ? <Link key={`${crumb.to}-${index}`} component={RouterLink} to={crumb.to} color="inherit" underline="hover">{crumb.label}</Link>
        : <Typography key={`${crumb.label}-${index}`} variant="s3" color="text.secondary" aria-current={index === crumbs.length - 1 ? 'page' : undefined}>{crumb.label}</Typography>)}
    </Breadcrumbs>}
    <PageHeader title={title} description={purpose} badge={badge} actions={actions} sx={{ mb: 0, flexDirection: { xs: 'column', sm: 'row' }, flexWrap: 'wrap', gap: 1.5, '& > .MuiStack-root': { minWidth: 0, maxWidth: 1, ml: { xs: 0 } }, '& h1': { whiteSpace: 'normal', overflowWrap: 'anywhere' } }} />
    {identity}
    {tabs && <Box sx={{ minWidth: 0 }}>{tabs}</Box>}
  </Stack>;
}

ObjectHeader.propTypes = {
  crumbs: PropTypes.arrayOf(PropTypes.shape({ label: PropTypes.string.isRequired, to: PropTypes.string })),
  title: PropTypes.node.isRequired,
  purpose: PropTypes.node,
  badge: PropTypes.node,
  identity: PropTypes.node,
  actions: PropTypes.node,
  tabs: PropTypes.node,
  sx: PropTypes.object,
};
