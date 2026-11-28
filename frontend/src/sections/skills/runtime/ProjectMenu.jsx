import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { Button, Link, ListItemText, Menu, MenuItem } from '@mui/material';
import { buildSkillTraceLink } from 'src/sections/skills/utils/traces';
import { CHOOSE_PROJECT, projectLine } from './runtimeCopy';

const stop = (event) => event.stopPropagation();

/**
 * A number that covers several projects opens this small menu: one line per
 * project with its counts ("acme-support-production · 1,204 traces · 31
 * failed"); choosing a line opens Observe for that project in the same tab
 * (UX-03). No dialog.
 *
 * `options` are the row's `projects` from the server. Each option opens the
 * row's link object for that project, unless the option carries its own
 * `link` (failed loads keep one link per project).
 */
export default function ProjectMenu({ children, what, link, kind = 'all', options, primary = false }) {
  const navigate = useNavigate();
  const menuId = useId();
  const [anchor, setAnchor] = useState(null);
  const targets = options
    // A comparison's primary action must open exactly the per-project cohort
    // returned for that metric, never a broader top-level fallback.
    .filter(option => !primary || (option.projectId && option.link?.project_id === option.projectId))
    .map((option) => ({
      ...option,
      href: buildSkillTraceLink({ link: option.link || link, kind: option.link ? 'all' : kind, projectId: option.projectId }),
    }))
    .filter((option) => option.href);
  if (!targets.length) return primary ? null : <>{children}</>;
  const Trigger = primary ? Button : Link;
  return (
    <>
      <Trigger
        component="button"
        type="button"
        {...(primary ? { variant: 'contained' } : { underline: 'hover', color: 'inherit' })}
        aria-label={primary ? what : CHOOSE_PROJECT(what)}
        aria-haspopup="menu"
        aria-controls={anchor ? menuId : undefined}
        aria-expanded={anchor ? 'true' : 'false'}
        onClick={(event) => { stop(event); setAnchor(event.currentTarget); }}
        sx={primary ? { width: { xs: 1, sm: 'auto' } } : { font: 'inherit', fontVariantNumeric: 'tabular-nums', verticalAlign: 'baseline' }}
      >
        {children}
      </Trigger>
      <Menu id={menuId} anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)} onClick={stop}>
        {targets.map((option) => (
          <MenuItem key={option.projectId} onClick={() => { setAnchor(null); navigate(option.href); }}>
            <ListItemText primary={projectLine(option)} primaryTypographyProps={{ variant: 's1' }} />
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}

ProjectMenu.propTypes = {
  children: PropTypes.node.isRequired,
  what: PropTypes.string.isRequired,
  link: PropTypes.object,
  kind: PropTypes.string,
  primary: PropTypes.bool,
  options: PropTypes.arrayOf(PropTypes.shape({
    projectId: PropTypes.string.isRequired,
    projectName: PropTypes.string,
    traces: PropTypes.number,
    failedRequests: PropTypes.oneOfType([PropTypes.number, PropTypes.object]),
    link: PropTypes.object,
  })).isRequired,
};
