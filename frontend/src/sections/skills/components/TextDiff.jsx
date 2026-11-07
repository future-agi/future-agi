import { Fragment, useMemo } from 'react';
import PropTypes from 'prop-types';
import { Box, Typography } from '@mui/material';
import { buildDiffRows } from './diffRows';

function DiffCell({ line, side }) {
  const role = line?.status === 'added' ? 'success' : 'danger';
  const changed = line && line.status !== 'unchanged';
  return <Box role="cell" data-status={line?.status || 'filler'} aria-label={line ? `${side} line ${line.number}, ${line.status}` : `${side}, no line`} sx={{ minWidth: 0, display: 'flex', gap: 1, p: 0.5, bgcolor: changed ? `status.${role}.bg` : 'background.paper', borderBottom: 1, borderColor: 'divider' }}>
    <Typography component="span" variant="s3" aria-hidden="true" sx={{ minWidth: '4ch', textAlign: 'right', color: 'text.secondary', fontVariantNumeric: 'tabular-nums', userSelect: 'none' }}>{line?.number}</Typography>
    <Typography component="code" variant="s1" sx={{ fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', minWidth: 0 }}>
      {line?.parts ? line.parts.map((part, index) => <Box component="span" key={index} sx={{ bgcolor: part.added || part.removed ? `status.${role}.bg` : 'transparent', fontWeight: part.added || part.removed ? 600 : 400 }}>{part.value}</Box>) : line?.text}
    </Typography>
  </Box>;
}
DiffCell.propTypes = { line: PropTypes.object, side: PropTypes.string.isRequired };

export default function TextDiff({ fromText = '', toText = '', fromTitle = 'Before', toTitle = 'After' }) {
  const rows = useMemo(() => buildDiffRows(fromText, toText), [fromText, toText]);
  if (!rows.length) return <Typography variant="s3" color="text.secondary">Both files are empty.</Typography>;
  return <Box role="table" aria-label="File changes" sx={{ minWidth: 0, border: 1, borderColor: 'divider', borderRadius: 1, overflow: 'hidden' }}>
    <Box role="row" sx={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', bgcolor: 'background.neutral' }}>
      {[fromTitle, toTitle].map((title, index) => <Typography key={index} role="columnheader" variant="m3" sx={{ p: 1, overflowWrap: 'anywhere' }}>{title}</Typography>)}
    </Box>
    {rows.map((row, index) => <Fragment key={index}>
      <Box role="row" sx={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)' }}><DiffCell line={row.left} side="Before" /><DiffCell line={row.right} side="After" /></Box>
    </Fragment>)}
    {fromText.endsWith('\n') !== toText.endsWith('\n') && <Typography variant="s3" sx={{ p: 1 }} color="text.secondary">{fromText.endsWith('\n') ? 'After' : 'Before'} has no newline at the end of the file.</Typography>}
  </Box>;
}
TextDiff.propTypes = { fromText: PropTypes.string, toText: PropTypes.string, fromTitle: PropTypes.string, toTitle: PropTypes.string };
