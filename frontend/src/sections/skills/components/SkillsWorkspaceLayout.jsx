import { Suspense, useId, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Box, IconButton, ListItemIcon, Menu, MenuItem, Stack, Tab, useMediaQuery, useTheme } from '@mui/material';
import CustomTooltip from 'src/components/tooltip';
import { FalconIcon, useOpenFalconAI } from 'src/sections/falcon-ai/components/FalconAIFab';
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
import { skillsRouteSx } from './routeGutter';
import './skillIcons';

// The assistant, from sm up, as a quiet icon in the navigation row instead of
// a button floating over content (UX iteration 3, SH2). Same icon, name and
// action as the floating button; Cmd+K still opens it from anywhere.
function AssistantButton() {
  const open = useOpenFalconAI();
  return <CustomTooltip title="Falcon AI (⌘K)" show placement="bottom" arrow>
    <IconButton size="small" onClick={open} sx={{ flexShrink: 0, color: 'text.secondary' }}><FalconIcon sx={{ fontSize: 20 }} /></IconButton>
  </CustomTooltip>;
}

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

  // Phones keep the floating assistant, so the end of the page clears it; from sm up it sits in this row.
  // The workspace never grows wider than the content panel, so nothing in it can scroll the app frame sideways.
  return <Stack key={access.identity} data-skills-workspace sx={{ minWidth: 0, maxWidth: '100%', width: '100%', minHeight: 0, flex: 1, pb: { xs: 9, sm: 3 } }}>
    <Box component="nav" aria-label="Skills navigation" sx={{ px: { xs: 2, md: 3 }, pt: 1.5, minWidth: 0 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <CustomTabs ref={tabsRef} value={tabValue} {...scrollTabsProps} aria-label="Skills views" sx={{ ...scrollTabsSx, flex: 1 }}>
          {tabs.map(([path, label]) => <Tab key={path} value={path} label={narrow && shortLabels[path] ? shortLabels[path] : label} aria-label={shortLabels[path] ? label : undefined} component={NavLink} to={path} end />)}
        </CustomTabs>
        {!narrow && <AssistantButton />}
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
  </Stack>;
}

export default function SkillsWorkspaceLayout() {
  return <SkillsProductionProvider><SkillsNavigation /></SkillsProductionProvider>;
}
