import { useMemo } from 'react';
import PropTypes from 'prop-types';
import { Box, Typography, useMediaQuery, useTheme } from '@mui/material';
import { buildDiffRows } from './diffRows';

function DiffCell({ line, side, unified = false }) {
  const role = line?.status === 'added' ? 'success' : 'danger';
  const changed = line && line.status !== 'unchanged';
  return <Box role={unified ? 'group' : 'cell'} data-status={line?.status || 'filler'} aria-label={line ? `${side} line ${line.number}, ${line.status}` : `${side}, no line`} sx={{ minWidth: 0, display: 'flex', gap: 1, p: 0.5, bgcolor: changed ? `status.${role}.bg` : 'background.paper', borderBottom: 1, borderColor: 'divider' }}>
    <Typography component="span" variant="s3" aria-hidden="true" sx={{ minWidth: '4ch', textAlign: 'right', color: 'text.secondary', fontVariantNumeric: 'tabular-nums', userSelect: 'none' }}>{line?.number}</Typography>
    {unified && <Typography component="span" variant="s1" aria-label={line.status} sx={{ fontFamily: 'monospace', width: '1ch', flexShrink: 0 }}>{line.status === 'added' ? '+' : line.status === 'removed' ? '−' : ' '}</Typography>}
    <Typography component="code" variant="s1" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', minWidth: 0 }}>
      {line?.parts ? line.parts.map((part, index) => <Box component="span" key={index} sx={{ bgcolor: part.added || part.removed ? `status.${role}.bg` : 'transparent', fontWeight: part.added || part.removed ? 600 : 400 }}>{part.value}</Box>) : line?.text}
    </Typography>
  </Box>;
}
DiffCell.propTypes = { line: PropTypes.object, side: PropTypes.string.isRequired, unified: PropTypes.bool };

/** One responsive renderer for publication, recovery, and exact release review. */
export default function TextDiff({ fromText = '', toText = '', fromTitle = 'Before', toTitle = 'After' }) {
  const theme = useTheme();
  const narrow = useMediaQuery(theme.breakpoints.down('sm'));
  const rows = useMemo(() => buildDiffRows(fromText, toText), [fromText, toText]);
  if (!rows.length) return <Typography variant="s3" color="text.secondary">Both files are empty.</Typography>;
  const contents = <Box role={narrow ? undefined : 'table'} aria-label={narrow ? undefined : 'File changes'} sx={{ minWidth: 0, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
    <Box role={narrow ? undefined : 'row'} sx={{ display: 'grid', gridTemplateColumns: narrow ? 'minmax(0, 1fr)' : 'minmax(0, 1fr) minmax(0, 1fr)', bgcolor: 'background.neutral' }}>
      {(narrow ? [`− ${fromTitle} / + ${toTitle}`] : [fromTitle, toTitle]).map((title, index) => <Typography key={index} role={narrow ? undefined : 'columnheader'} variant="m3" sx={{ p: 1, overflowWrap: 'anywhere' }}>{title}</Typography>)}
    </Box>
    {rows.map((row, index) => narrow ? (
      <Box key={index}>
        {(row.left?.status === 'unchanged' ? [row.left] : [row.left, row.right].filter(Boolean)).map((line) => <Box key={line.status}>
          <DiffCell unified line={line} side={line.status === 'added' ? 'After' : line.status === 'removed' ? 'Before' : 'Unchanged'} />
        </Box>)}
      </Box>
    ) : (
      <Box key={index} role="row" sx={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}><DiffCell line={row.left} side="Before" /><DiffCell line={row.right} side="After" /></Box>
    ))}
    {fromText.endsWith('\n') !== toText.endsWith('\n') && <Typography variant="s3" sx={{ p: 1 }} color="text.secondary">{fromText.endsWith('\n') ? toTitle : fromTitle} has no newline at the end of the file.</Typography>}
  </Box>;
  return narrow ? <Box role="region" aria-label="Unified file changes">{contents}</Box> : contents;
}
TextDiff.propTypes = { fromText: PropTypes.string, toText: PropTypes.string, fromTitle: PropTypes.string, toTitle: PropTypes.string };
