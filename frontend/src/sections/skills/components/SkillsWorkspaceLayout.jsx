import { Suspense, useId, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Box, GlobalStyles, IconButton, ListItemIcon, Menu, MenuItem, Stack, Tab } from '@mui/material';
import { alpha } from '@mui/material/styles';
import CustomTooltip from 'src/components/tooltip';
import { FalconIcon, useOpenFalconAI } from 'src/sections/falcon-ai/components/FalconAIFab';
import { useContextAuthoring } from '../contexts/ContextLibraryView';
import { CustomTabs } from 'src/components/tabs/tabs';
import ContentSkeleton from 'src/components/content-skeleton';
import Iconify from 'src/components/iconify';
import { paths } from 'src/routes/paths';
import { spendPolicyReady } from 'src/api/skills/r3-spend';
import { isContractedApiPath } from 'src/api/contracts/api-surface';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { campaignsContractReady, useContextCampaignAvailability } from 'src/api/skills/context-campaigns';
import { useSkillsAccess } from '../utils/access';
import SkillsProductionProvider from './SkillsProductionProvider';
import { scrollTabsProps, scrollTabsSx, useActiveTabInView } from './tabStrip';
import { skillsRouteSx } from './routeGutter';
import './skillIcons';

// The assistant as a quiet 32px icon (medium: 20px icon + 6px padding) in the
// navigation row, at every width,
// instead of a button floating over content (UX iteration 4, SH1). Same icon
// and action as the floating button elsewhere; Cmd+K still opens it.
function AssistantButton() {
  const open = useOpenFalconAI();
  return <CustomTooltip title="Falcon AI (⌘K)" show placement="bottom" arrow>
    <IconButton size="medium" aria-label="Ask Falcon AI" onClick={open} sx={{ flexShrink: 0, color: 'text.secondary' }}><FalconIcon sx={{ fontSize: 20 }} /></IconButton>
  </CustomTooltip>;
}

// Inline links and multiline fields inside Skills (UX iteration 4, SH5, SH6).
// - Links: the DS dark palette maps primary.main to near-white (#EFEFF1) and the
//   app theme's MuiLink default is underline="hover", so in dark mode an inline
//   Link read as plain white text. Inside Skills an inline link always shows the
//   DS link colour (text.link) at normal weight with a soft underline. Links that
//   opted out (underline="none"), breadcrumbs, record-row links (tables, grids,
//   lists) and links inside buttons keep their own look.
// - Multiline fields: the root kept MUI's 14px side padding and the textarea added
//   the theme's 12px, so their text sat ~24px in while single-line text sat 12px.
//   The root now carries the single-line padding and the textarea none.
// Dialogs and temporary drawers render outside the frame (portals), so while a
// Skills route is mounted the same rules also apply inside them.
const OVERLAYS = '.MuiDialog-root, .MuiDrawer-modal';
// Record rows keep their own object-name links: MUI table rows, data-grid rows
// (role="row") and list rows (li: library cards, breadcrumbs, phone lists).
const INLINE_LINK = '& .MuiLink-root:not(.MuiLink-underlineNone):not(.MuiBreadcrumbs-root .MuiLink-root):not(.MuiTableRow-root .MuiLink-root):not([role="row"] .MuiLink-root):not(li .MuiLink-root):not(.MuiButtonBase-root .MuiLink-root)';
const workspaceSx = (theme) => {
  const link = theme.palette.text.link ?? theme.palette.primary.main;
  return {
    [INLINE_LINK]: {
      color: link,
      fontWeight: theme.typography.fontWeightRegular,
      textDecoration: 'underline',
      textDecorationColor: alpha(link, 0.4),
      textUnderlineOffset: '2px',
      '&:hover, &:focus-visible': { textDecorationColor: link },
    },
    '& .MuiInputBase-multiline': {
      paddingLeft: '12px',
      paddingRight: '12px',
      '&.MuiInputBase-sizeSmall': { paddingLeft: '10px', paddingRight: '10px' },
      '& textarea': { paddingLeft: 0, paddingRight: 0 },
    },
  };
};

// Local navigation within Skills. Destination visibility never grants access:
// direct routes and their existing authorization remain unchanged.
function SkillsNavigation() {
  const { pathname } = useLocation();
  const access = useSkillV11Scope();
  const composition = useContextAuthoring();
  const campaigns = useContextCampaignAvailability();
  const [menu, setMenu] = useState(null);
  const menuId = useId();
  const role = useSkillsAccess();
  const tabsRef = useRef(null);
  const bindings = composition?.bindings;
  const available = {
    [paths.dashboard.skills.root]: true,
    [paths.dashboard.skills.contextRollouts]: access.enabled && campaignsContractReady() && campaigns.data?.enabled === true,
    [paths.dashboard.skills.allowance]: access.enabled && spendPolicyReady(),
    [paths.dashboard.skills.contexts]: Boolean(bindings?.library && bindings?.detail && bindings?.editor),
    // Viewers can't act on reviews, so the tab would only lead to "You don't have access".
    // The direct URL still renders that explanation; only the tab is hidden.
    [paths.dashboard.skills.reviews]: Boolean(bindings?.reviews) && !(role.ready && role.isViewer),
    // Archived skills (A8): workspace admins only, and only once the archived-skills API is in
    // the generated contract. Until then nothing new renders; the direct route keeps its own states.
    [paths.dashboard.skills.archived]: access.enabled && isContractedApiPath('/model-hub/skills/archived/') && role.ready && role.canAdmin,
  };
  const tabs = [
    [paths.dashboard.skills.root, 'Skills'],
    [paths.dashboard.skills.contexts, 'Agent contexts'],
    [paths.dashboard.skills.reviews, 'Reviews'],
    [paths.dashboard.skills.contextRollouts, 'Repository rollouts'],
  ].filter(([path]) => available[path]);
  const more = [
    [paths.dashboard.skills.allowance, 'Workspace allowance', 'solar:wallet-linear'],
    [paths.dashboard.skills.archived, 'Archived skills', 'solar:archive-linear'],
  ].filter(([path]) => available[path]);
  const matches = (path) => pathname === path || pathname.startsWith(`${path}/`);
  // Match the deepest route, not just the Skills prefix. Hidden direct routes
  // deliberately select no tab, rather than misrepresenting another page.
  const selected = Object.keys(available).filter(matches).sort((a, b) => b.length - a.length)[0];
  const moreSelected = more.some(([path]) => path === selected);
  const tabValue = tabs.some(([path]) => path === selected) ? selected : false;
  const menuOpen = Boolean(menu && menu.identity === access.identity && menu.pathname === pathname);
  const closeMenu = () => setMenu(null);
  useActiveTabInView(tabsRef, tabValue);

  // No floating assistant in Skills, so the page end needs no extra clearance at any width.
  // The workspace never grows wider than the content panel, so nothing in it can scroll the app frame sideways.
  return <><GlobalStyles styles={(theme) => ({ [OVERLAYS]: workspaceSx(theme) })} /><Stack key={access.identity} data-skills-workspace sx={[{ minWidth: 0, maxWidth: '100%', width: '100%', minHeight: 0, flex: 1, pb: 3 }, workspaceSx]}>
    <Box component="nav" aria-label="Skills navigation" sx={{ px: { xs: 2, md: 3 }, pt: 1.5, minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <CustomTabs ref={tabsRef} value={tabValue} {...scrollTabsProps} aria-label="Skills views" sx={{ ...scrollTabsSx, flex: 1 }}>
          {/* One label at every width; the strip scrolls and fades a cut tab. */}
          {tabs.map(([path, label]) => <Tab key={path} value={path} label={label} component={NavLink} to={path} end />)}
        </CustomTabs>
        <AssistantButton />
        {more.length > 0 && <>
          <IconButton
            id={`${menuId}-trigger`} size="small" aria-label="More Skills pages"
            aria-haspopup="menu" aria-controls={menuOpen ? menuId : undefined}
            aria-expanded={menuOpen} aria-current={moreSelected ? 'page' : undefined}
            onClick={(event) => setMenu({ anchor: event.currentTarget, identity: access.identity, pathname })}
            sx={{ flexShrink: 0, bgcolor: moreSelected ? 'action.selected' : undefined }}
          ><Iconify icon="solar:menu-dots-bold" /></IconButton>
          {/* Opens below the trigger, right-aligned to it, so it never covers the page header's actions. */}
          <Menu
            id={menuId} anchorEl={menu?.anchor} open={menuOpen} onClose={closeMenu} MenuListProps={{ 'aria-labelledby': `${menuId}-trigger` }}
            anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }} transformOrigin={{ vertical: 'top', horizontal: 'right' }}
            slotProps={{ paper: { sx: { mt: 1 } } }}
          >
            {/* 16px icon + 12px gap (MenuItem's own icon rule outranks an sx on ListItemIcon). */}
            {more.map(([path, label, icon]) => <MenuItem key={path} component={NavLink} to={path} selected={selected === path} onClick={closeMenu} sx={{ '& .MuiListItemIcon-root': { minWidth: 28, mr: 0 } }}>
              <ListItemIcon><Iconify icon={icon} width={16} /></ListItemIcon>{label}
            </MenuItem>)}
          </Menu>
        </>}
      </Stack>
    </Box>
    <Suspense fallback={<Box sx={skillsRouteSx}><ContentSkeleton variant="page" sx={{ overflow: 'hidden' }} /></Box>}>
      <Outlet />
    </Suspense>
  </Stack></>;
}

export default function SkillsWorkspaceLayout() {
  return <SkillsProductionProvider><SkillsNavigation /></SkillsProductionProvider>;
}
