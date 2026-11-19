import { cloneElement, isValidElement, useRef } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Breadcrumbs, Link, Stack, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import PageHeader from 'src/components/page-header';
import { scrollTabsProps, scrollTabsSx, useActiveTabInView } from './tabStrip';

const crumbSx = (index, count) => {
  const last = index === count - 1;
  const middle = index > 0 && !last;
  return {
    display: 'block', minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
    // On phones the middle crumb gives way first; the current page keeps the rest.
    ...(middle ? { maxWidth: { xs: 96, sm: 240 } } : {}),
    ...(index === 0 && count > 1 ? { flexShrink: 0 } : {}),
  };
};

// Below sm the purpose keeps to two lines; the full text stays in the DOM (and in `title`).
const CLAMP_SX = { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden', overflowWrap: 'anywhere' };

export default function ObjectHeader({ crumbs = [], title, purpose, badge, identity, actions, tabs, sx }) {
  const theme = useTheme();
  // One phone layout for every object (UX iteration 5, SH3): title, purpose (two
  // lines), identity, then the actions on their own left-aligned row. Desktop is unchanged.
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const tabsRef = useRef(null);
  const strip = isValidElement(tabs) && typeof tabs.type !== 'string';
  useActiveTabInView(tabsRef, strip ? tabs.props.value : undefined);
  // Object tabs scroll on phones, start on the route gutter, and keep the active tab in view.
  const tabStrip = strip
    ? cloneElement(tabs, { ...scrollTabsProps, sx: [scrollTabsSx, ...(Array.isArray(tabs.props.sx) ? tabs.props.sx : [tabs.props.sx])] })
    : tabs;
  return <Stack useFlexGap spacing={1.5} sx={{ minWidth: 0, ...sx }}>
    {/* The breadcrumb stays at every width: one line, the middle crumb truncates. */}
    {crumbs.length > 0 && <Box sx={{ minWidth: 0 }}>
      <Breadcrumbs aria-label="Breadcrumb" sx={{ typography: 's3', minWidth: 0, '& .MuiBreadcrumbs-ol': { flexWrap: 'nowrap' }, '& .MuiBreadcrumbs-li': { minWidth: 0 }, '& .MuiBreadcrumbs-li:first-of-type': { flexShrink: 0 }, '& .MuiBreadcrumbs-separator': { flexShrink: 0 } }}>
        {crumbs.map((crumb, index) => index < crumbs.length - 1 && crumb.to
          ? <Link key={`${crumb.to}-${index}`} component={RouterLink} to={crumb.to} color="inherit" underline="hover" title={crumb.label} sx={crumbSx(index, crumbs.length)}>{crumb.label}</Link>
          : <Typography key={`${crumb.label}-${index}`} variant="s3" color="text.secondary" title={crumb.label} aria-current={index === crumbs.length - 1 ? 'page' : undefined} sx={crumbSx(index, crumbs.length)}>{crumb.label}</Typography>)}
      </Breadcrumbs>
    </Box>}
    {/* Actions are buttons only and sit vertically centred against the title block. */}
    <PageHeader title={title} description={phone && purpose ? <Box component="span" title={typeof purpose === 'string' ? purpose : undefined} sx={CLAMP_SX}>{purpose}</Box> : purpose} badge={badge} actions={phone ? undefined : actions} sx={{ mb: 0, flexDirection: { xs: 'column', sm: 'row' }, alignItems: { xs: 'flex-start', sm: 'center' }, flexWrap: 'wrap', gap: 1.5, '& > .MuiStack-root': { minWidth: 0, maxWidth: 1, ml: { xs: 0 } }, '& > .MuiStack-root:last-of-type': { flexWrap: 'wrap', rowGap: 1 }, '& h1': { whiteSpace: 'normal', overflowWrap: 'anywhere' } }} />
    {identity}
    {/* Phone: the actions follow the identity on their own row, starting at the content edge
        (a caller's own action group is left-aligned too, so both objects match). */}
    {phone && actions && <Stack data-object-actions direction="row" alignItems="center" useFlexGap sx={{ flexWrap: 'wrap', gap: 1, minWidth: 0, justifyContent: 'flex-start', '& > .MuiStack-root': { justifyContent: 'flex-start', flexWrap: 'wrap' } }}>{actions}</Stack>}
    {/* Measured (UX iteration 3, SH1): the panel's first line sat 4px under the
        strip at 390 against 12px at 1440 and up; the phone margin evens it out. */}
    {tabs && <Box ref={tabsRef} sx={{ minWidth: 0, mb: { xs: 1, sm: 0 } }}>{tabStrip}</Box>}
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
