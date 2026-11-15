import { Fragment, useLayoutEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { Box, Button, Checkbox, Chip, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';
import { runStatusOf } from 'src/utils/runStatus';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { evidenceCoverage, evidenceOutcome } from '../utils/review-display';
import { stackedTableSx } from './responsiveTable';
import When from './When';

// Missing results are "Not recorded", never a pass. A result the page could not
// load (a renewed or stale review) is "Not checked yet": renewing rechecks it.
// Neither is "Not recorded", and neither is a pass.
const NOT_RECORDED = { status: 'not_recorded', role: 'neutral', label: 'Not recorded' };
const NOT_LOADED = { status: 'not_loaded', role: 'neutral', label: 'Not checked yet' };
const resultBadge = (source, status) => {
  const badge = evidenceOutcome({ source, outcome: status }) || NOT_RECORDED;
  return { status: badge.status, role: badge.role, label: badge.label ?? runStatusOf(badge.status).label };
};
// Short cells keep their words whole ("Workspace", never "Workspac / e"); column
// minimums apply to the desktop table only, not the stacked phone list.
const nowrap = (minWidth) => ({ whiteSpace: 'nowrap', ...(minWidth ? { minWidth: { sm: minWidth } } : {}) });
const kinds = { text_comparison: 'Answer check', application_verification: 'Application test', external_pipeline: 'External pipeline' };
// Below this container width the table becomes stacked rows (UX iteration 4,
// SH4). The component measures its own box, so a dialog or a side column
// stacks too, not only a phone.
const STACK_BELOW = 720;
const VISIBILITY = { private: runStatusOf('private').label, workspace: 'Workspace' };

// Returns [stacked, ref]; the ref goes on a wrapper that stays mounted in both layouts.
function useStacked() {
  const [node, setNode] = useState(null);
  const [stacked, setStacked] = useState(false);
  useLayoutEffect(() => {
    if (!node) return undefined;
    // Unmeasured (0, e.g. before layout) keeps the table.
    const measure = () => { const width = node.getBoundingClientRect().width; setStacked(width > 0 && width < STACK_BELOW); };
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return [stacked, setNode];
}

// Display and controlled selection only. Sharing always belongs to the caller,
// which must explicitly confirm it; neither selection nor Inspect shares data.
// A private row carries the Private badge only; the caller states once, above
// the list, that private results must be shared before they can be attached.
export default function EvidenceSummary({ items = [], selectable = false, selected = [], onToggle, onInspect, renderAction, emptyText = 'No matching test evidence. Not tested is not a pass.' }) {
  const [stacked, boxRef] = useStacked();
  const selections = selected instanceof Set ? selected : new Set(selected);
  if (!items.length) return <Typography variant="s1" color="text.secondary">{emptyText}</Typography>;
  const rows = items.map((item) => {
    const key = `${item.source}:${item.id}`;
    const checked = selections.has(key) || selections.has(item.id);
    const title = item.title || kinds[item.kind] || 'Test result';
    const notLoaded = item.resultState === 'not_loaded';
    const result = notLoaded ? null : item.outcome || (item.status === 'completed' ? null : item.status);
    const checkbox = selectable && <Checkbox size="small" checked={checked} disabled={!onToggle || (!checked && item.visibility === 'private')} onChange={() => onToggle(item)} inputProps={{ 'aria-label': `Attach ${title}` }} />;
    const titleLine = <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap" sx={{ minWidth: 0 }}><Typography variant="s1">{title}</Typography>{item.origin === 'from_test' ? <Chip size="small" variant="outlined" label="From your test" /> : item.suggested && <Chip size="small" variant="outlined" label="Suggested" />}</Stack>;
    const notes = <>
      {notLoaded
        ? <Typography variant="s3" color="text.secondary">Rechecked when you renew.</Typography>
        : !result && !item.results?.length && <Typography variant="s3" color="text.secondary">Result not recorded; inspect before relying on it.</Typography>}
      {item.targetMatch !== undefined && <Typography variant="s3" color="text.secondary">{item.targetMatch ? 'Matches target' : 'Different target'}</Typography>}
      {renderAction?.(item)}
    </>;
    const perCheck = !notLoaded && item.results?.length > 0;
    const badge = notLoaded ? <StatusBadge {...NOT_LOADED} /> : perCheck ? <Stack spacing={0.5}>{item.results.map((outcome) => <Stack key={outcome.label} direction="row" gap={0.5} alignItems="center"><Typography variant="s3">{outcome.label}</Typography><StatusBadge {...resultBadge(item.source, outcome.status)} /></Stack>)}</Stack> : <StatusBadge {...resultBadge(item.source, result)} />;
    const coverage = evidenceCoverage(item.counts, item.cases);
    // A row action: a small text button (the design system's table-row style).
    const inspect = <Button size="small" variant="text" disabled={!onInspect} onClick={() => onInspect(item)} sx={{ flexShrink: 0 }}>Inspect</Button>;
    return { item, key, checked, checkbox, titleLine, notes, perCheck, badge, coverage, inspect };
  });

  if (stacked) {
    // Stacked rows: title with the result on the right; coverage; visibility and
    // expiry with Inspect on the right. Same controls and names as the table.
    return <Box ref={boxRef} sx={{ minWidth: 0, maxWidth: 1 }}><Box component="ul" aria-label="Test evidence" sx={{ m: 0, p: 0, listStyle: 'none' }}>
      {rows.map(({ item, key, checked, checkbox, titleLine, notes, perCheck, badge, coverage, inspect }) => {
        const visibility = VISIBILITY[item.visibility] ?? 'Visibility not reported';
        return <Stack key={key} component="li" direction="row" gap={1} data-selected={selectable && checked ? 'true' : undefined}
          sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider', minWidth: 0, ...(selectable && checked ? { bgcolor: 'action.selected' } : {}) }}>
          {checkbox && <Box sx={{ flexShrink: 0, mt: -0.75, ml: -0.75 }}>{checkbox}</Box>}
          <Stack spacing={0.5} sx={{ flex: 1, minWidth: 0 }}>
            <Stack direction="row" alignItems="flex-start" justifyContent="space-between" gap={1}>
              {titleLine}
              {!perCheck && <Box sx={{ flexShrink: 0 }}>{badge}</Box>}
            </Stack>
            {perCheck && badge}
            {notes}
            {/* Without the column header a bare "Not reported" reads as nothing; name what is missing. */}
            <Typography variant="s3" color="text.secondary">{coverage === 'Not reported' ? 'Coverage not reported' : coverage}</Typography>
            <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
              <Typography component="span" variant="s3" color="text.secondary" sx={{ minWidth: 0 }}>
                {/* " · " only between parts that exist, so no line starts with a separator. */}
                {[visibility, <When key="expires" value={item.expiresAt} kind="expiry" fallback="Expiry not reported" />].filter(Boolean).map((part, index) => <Fragment key={index}>{index > 0 && ' · '}{part}</Fragment>)}
              </Typography>
              {inspect}
            </Stack>
          </Stack>
        </Stack>;
      })}
    </Box></Box>;
  }

  return <Box ref={boxRef} sx={{ minWidth: 0, maxWidth: 1 }}><TableContainer sx={{ minWidth: 0, maxWidth: 1 }}>
    <Table size="small" aria-label="Test evidence" sx={stackedTableSx}>
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        {selectable && <TableCell padding="checkbox">Attach</TableCell>}
        {['Evidence', 'Result', 'Coverage', 'Visibility', 'Expires'].map((label) => <TableCell key={label} sx={{ whiteSpace: 'nowrap' }}>{label}</TableCell>)}
        {/* The Inspect buttons name themselves; the column header only needs a name for assistive technology. */}
        <TableCell aria-label="Actions" />
      </TableRow></TableHead>
      <TableBody>{rows.map(({ item, key, checked, checkbox, titleLine, notes, badge, coverage, inspect }) => <TableRow key={key} hover selected={selectable && checked} sx={{ height: ROW_HEIGHT.default }}>
        {selectable && <TableCell padding="checkbox">{checkbox}</TableCell>}
        <TableCell sx={{ minWidth: 180 }}><Stack spacing={0.5}>{titleLine}{notes}</Stack></TableCell>
        {/* Result is the badge only (per-check results keep their check name beside each badge). */}
        <TableCell sx={nowrap(140)}>{badge}</TableCell>
        <TableCell sx={{ minWidth: { sm: 160 } }}><Typography variant="s3">{coverage}</Typography></TableCell>
        <TableCell sx={nowrap(110)}>{item.visibility === 'private' ? <StatusBadge status="private" /> : item.visibility === 'workspace' ? <StatusBadge status="workspace" role="neutral" label="Workspace" /> : <Typography component="span" variant="s3">Not reported</Typography>}</TableCell>
        {/* Under the "Expires" header the cell reads "in 29 days" (the stacked row keeps "Expires in 29 days"). */}
        <TableCell sx={nowrap(120)}><Typography component="span" variant="s3"><When value={item.expiresAt} kind="expiry-short" /></Typography></TableCell>
        <TableCell data-cell="actions" sx={nowrap()}>{inspect}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
  </TableContainer></Box>;
}

EvidenceSummary.propTypes = {
  items: PropTypes.arrayOf(PropTypes.object),
  selectable: PropTypes.bool,
  selected: PropTypes.oneOfType([PropTypes.arrayOf(PropTypes.string), PropTypes.instanceOf(Set)]),
  onToggle: PropTypes.func,
  onInspect: PropTypes.func,
  renderAction: PropTypes.func,
  emptyText: PropTypes.string,
};
