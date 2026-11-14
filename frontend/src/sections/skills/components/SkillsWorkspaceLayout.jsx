import { Suspense, useId, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Box, IconButton, ListItemIcon, Menu, MenuItem, Stack, Tab, useMediaQuery, useTheme } from '@mui/material';
import { useContextAuthoring } from '../contexts/ContextLibraryView';
import { CustomTabs } from 'src/components/tabs/tabs';
import ContentSkeleton from 'src/components/content-skeleton';
import Iconify from 'src/components/iconify';
import { paths } from 'src/routes/paths';
import { spendPolicyReady } from 'src/api/skills/r3-spend';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { campaignsContractReady, useContextCampaignAvailability } from 'src/api/skills/context-campaigns';
import SkillsProductionProvider from './SkillsProductionProvider';
import { scrollTabsProps, scrollTabsSx, useActiveTabInView } from './tabStrip';
import './skillIcons';

// Local navigation within Skills. Destination visibility never grants access:
// direct routes and their existing authorization remain unchanged.
function SkillsNavigation() {
  const { pathname } = useLocation();
  const access = useSkillV11Scope();
  const composition = useContextAuthoring();
  const campaigns = useContextCampaignAvailability();
  const [menu, setMenu] = useState(null);
  const menuId = useId();
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('sm'));
  const tabsRef = useRef(null);
  const bindings = composition?.bindings;
  const available = {
    [paths.dashboard.skills.root]: true,
    [paths.dashboard.skills.contextRollouts]: access.enabled && campaignsContractReady() && campaigns.data?.enabled === true,
    [paths.dashboard.skills.allowance]: access.enabled && spendPolicyReady(),
    [paths.dashboard.skills.contexts]: Boolean(bindings?.library && bindings?.detail && bindings?.editor),
    [paths.dashboard.skills.reviews]: Boolean(bindings?.reviews),
    [paths.dashboard.skills.archived]: Boolean(bindings?.archived),
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
  // Phones show the short visible label; the accessible name stays the full one.
  const shortLabels = { [paths.dashboard.skills.contextRollouts]: 'Rollouts' };

  // Bottom padding = assistant button (40px) + 16px offset + 16px, so it never covers the last row.
  // The workspace never grows wider than the content panel, so nothing in it can scroll the app frame sideways.
  return <Stack key={access.identity} data-skills-workspace sx={{ minWidth: 0, maxWidth: '100%', width: '100%', minHeight: 0, flex: 1, pb: { xs: 9, md: 10 } }}>
    <Box component="nav" aria-label="Skills navigation" sx={{ px: { xs: 2, md: 3 }, pt: 1.5, minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <CustomTabs ref={tabsRef} value={tabValue} {...scrollTabsProps} aria-label="Skills views" sx={{ ...scrollTabsSx, flex: 1 }}>
          {tabs.map(([path, label]) => <Tab key={path} value={path} label={narrow && shortLabels[path] ? shortLabels[path] : label} aria-label={shortLabels[path] ? label : undefined} component={NavLink} to={path} end />)}
        </CustomTabs>
        {more.length > 0 && <>
          <IconButton
            id={`${menuId}-trigger`} size="small" aria-label="More Skills pages"
            aria-haspopup="menu" aria-controls={menuOpen ? menuId : undefined}
            aria-expanded={menuOpen} aria-current={moreSelected ? 'page' : undefined}
            onClick={(event) => setMenu({ anchor: event.currentTarget, identity: access.identity, pathname })}
            sx={{ flexShrink: 0, bgcolor: moreSelected ? 'action.selected' : undefined }}
          ><Iconify icon="solar:menu-dots-bold" /></IconButton>
          <Menu id={menuId} anchorEl={menu?.anchor} open={menuOpen} onClose={closeMenu} MenuListProps={{ 'aria-labelledby': `${menuId}-trigger` }}>
            {more.map(([path, label, icon]) => <MenuItem key={path} component={NavLink} to={path} selected={selected === path} onClick={closeMenu}>
              <ListItemIcon><Iconify icon={icon} width={16} /></ListItemIcon>{label}
            </MenuItem>)}
          </Menu>
        </>}
      </Stack>
    </Box>
    <Suspense fallback={<Box sx={{ px: { xs: 2, md: 3 }, pt: 1.5 }}><ContentSkeleton variant="page" sx={{ overflow: 'hidden' }} /></Box>}>
      <Outlet />
    </Suspense>
  </Stack>;
}

export default function SkillsWorkspaceLayout() {
  return <SkillsProductionProvider><SkillsNavigation /></SkillsProductionProvider>;
}
