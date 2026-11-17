import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Box, Link, MenuItem, TextField, Tooltip, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { buildSkillTraceLink } from 'src/sections/skills/utils/traces';
import ProjectMenu from './ProjectMenu';
import { DIAGNOSIS, WINDOW_LABEL, WINDOW_OPTIONS, lowSampleTitle } from './runtimeCopy';

// Small pieces shared by the runtime surfaces. Theme tokens only, so dark mode
// follows (§7.7).

const stop = (event) => event.stopPropagation();

/**
 * One number that opens exactly the traces it counted (UX-02). Same tab, via
 * the router. A link without a project (a version row across several
 * projects) opens the project menu instead (UX-03). Without a usable link the
 * number is plain text. `lowSample` renders it in the secondary colour.
 */
export function TraceNumber({ children, link, kind = 'all', projects, what, lowSample = false, traces, projectId }) {
  const sx = { fontVariantNumeric: 'tabular-nums', color: lowSample ? 'text.secondary' : undefined };
  const body = lowSample ? <Tooltip title={lowSampleTitle(traces ?? 0)}><span>{children}</span></Tooltip> : children;
  if (link && !(projectId ?? link.project_id) && projects?.length) {
    return <Box component="span" sx={sx}><ProjectMenu what={what} link={link} kind={kind} options={projects}>{body}</ProjectMenu></Box>;
  }
  const href = link ? buildSkillTraceLink({ link, kind, projectId }) : null;
  if (!href) return <Box component="span" sx={sx}>{body}</Box>;
  return (
    <Link component={RouterLink} to={href} onClick={stop} underline="hover" color={lowSample ? 'text.secondary' : 'inherit'} sx={sx} aria-label={what ? `${what}: open traces` : undefined}>
      {body}
    </Link>
  );
}
TraceNumber.propTypes = {
  children: PropTypes.node.isRequired,
  link: PropTypes.object,
  kind: PropTypes.string,
  projects: PropTypes.array,
  what: PropTypes.string,
  lowSample: PropTypes.bool,
  traces: PropTypes.number,
  projectId: PropTypes.string,
};

/** The one window control of a Runtime tab (24 hours / 7 days / 30 days). */
export function WindowSelect({ value, onChange, id }) {
  return (
    <TextField id={id} select size="small" label={WINDOW_LABEL} value={value} onChange={(event) => onChange(event.target.value)} sx={{ minWidth: 140 }}>
      {WINDOW_OPTIONS.map((option) => <MenuItem key={option.value} value={option.value}>{option.label}</MenuItem>)}
    </TextField>
  );
}
WindowSelect.propTypes = { value: PropTypes.string.isRequired, onChange: PropTypes.func.isRequired, id: PropTypes.string };

/**
 * Loading, error, denied and slow states shared by the runtime reads (§7.7).
 * Returns null when the query has data.
 */
export function RuntimeQueryState({ query, errorKind, errorTitle, deniedText, loadingLabel, rows = 5, compact = false }) {
  if (query.isPending && query.fetchStatus !== 'idle') {
    return <ContentSkeleton variant="rows" rows={rows} label={loadingLabel} />;
  }
  if (!query.isError) return null;
  if (errorKind === 'denied') {
    return <Typography variant="s1" color="text.secondary" role="status">{deniedText}</Typography>;
  }
  const title = errorKind === 'slow' ? DIAGNOSIS.readBudget : errorTitle;
  if (compact) {
    return (
      <Typography variant="s3" color="error" role="alert">
        {title}{' '}
        <Link component="button" type="button" underline="always" color="inherit" onClick={() => query.refetch()}>Retry</Link>
      </Typography>
    );
  }
  return <ErrorState title={title} onRetry={() => query.refetch()} sx={{ flex: '0 0 auto', height: 'auto', py: 3 }} />;
}
RuntimeQueryState.propTypes = {
  query: PropTypes.object.isRequired,
  errorKind: PropTypes.string,
  errorTitle: PropTypes.string.isRequired,
  deniedText: PropTypes.string,
  loadingLabel: PropTypes.string.isRequired,
  rows: PropTypes.number,
  compact: PropTypes.bool,
};

/** Relative time without the word "ago" spelled twice. */
export function relativeFrom(value, now = Date.now()) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return '';
  const seconds = Math.max(0, Math.round((now - time) / 1000));
  if (seconds < 60) return `${seconds} ${seconds === 1 ? 'second' : 'seconds'} ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}
