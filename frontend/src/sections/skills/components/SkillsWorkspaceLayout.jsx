import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Box, Stack, Tab } from '@mui/material';
import { useContextAuthoring } from '../contexts/ContextLibraryView';
import { CustomTabs } from 'src/components/tabs/tabs';
import { paths } from 'src/routes/paths';
import { spendPolicyReady } from 'src/api/skills/r3-spend';
import { useSkillV11Scope } from 'src/api/skills/testing';

// This is local navigation within Skills, not a second Agents product or the
// unrelated global notification popover. No guessed/visible-page unread count.
export default function SkillsWorkspaceLayout() {
  const { pathname } = useLocation();
  const access = useSkillV11Scope();
  const composition = useContextAuthoring();
  const bindings = composition?.bindings;
  const available = {
    [paths.dashboard.skills.root]: true,
    [paths.dashboard.skills.allowance]: access.enabled && spendPolicyReady(),
    [paths.dashboard.skills.contexts]: Boolean(bindings?.library && bindings?.detail && bindings?.editor),
    [paths.dashboard.skills.reviews]: Boolean(bindings?.reviews),
    [paths.dashboard.skills.archived]: Boolean(bindings?.archived),
  };
  let selected = paths.dashboard.skills.root;
  if (pathname.startsWith(paths.dashboard.skills.allowance)) selected = paths.dashboard.skills.allowance;
  if (pathname.startsWith(paths.dashboard.skills.contexts)) selected = paths.dashboard.skills.contexts;
  else if (pathname.startsWith(paths.dashboard.skills.reviews)) selected = paths.dashboard.skills.reviews;
  else if (pathname.startsWith(paths.dashboard.skills.archived)) selected = paths.dashboard.skills.archived;
  return <Stack key={access.identity} sx={{ minWidth: 0, minHeight: 0, flex: 1 }}>
    <Box component="nav" aria-label="Skills navigation" sx={{ px: { xs: 2, md: 3 }, pt: 1.5, minWidth: 0 }}>
      <CustomTabs value={selected} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile aria-label="Skills views">
        {[[paths.dashboard.skills.root, 'Skills'], [paths.dashboard.skills.contexts, 'Agent contexts'], [paths.dashboard.skills.reviews, 'Reviews'], [paths.dashboard.skills.archived, 'Archived identities'], [paths.dashboard.skills.allowance, 'Workspace allowance']].map(([path, label]) => <Tab key={path} value={path} label={available[path] ? label : `${label} (unavailable)`} disabled={!available[path]} component={available[path] ? NavLink : 'button'} {...(available[path] ? { to: path, end: true } : {})} />)}
      </CustomTabs>
    </Box>
    <Outlet />
  </Stack>;
}
