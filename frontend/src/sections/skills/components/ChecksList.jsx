import PropTypes from 'prop-types';
import { Box, List, ListItem, ListItemButton, Stack, Typography } from '@mui/material';
import StatusBadge from 'src/components/status-badge';

const groups = [
  { severity: 'error', title: 'Errors', status: 'failed' },
  { severity: 'warning', title: 'Warnings', status: 'warning' },
  { severity: 'info', title: 'Notes', status: 'completed' },
];

export default function ChecksList({ checks, onSelect, dense = false }) {
  if (!checks) return <Typography variant="s3" color="text.secondary">Checks have not run yet.</Typography>;
  if (!checks.items?.length) return <Typography variant="s1">No issues found.</Typography>;
  return (
    <Stack spacing={dense ? 1 : 2}>
      {groups.map(({ severity, title, status }) => {
        const items = checks.items.filter((item) => item.severity === severity);
        if (!items.length) return null;
        return (
          <Box component="section" aria-label={title} key={severity}>
            <Typography variant="m3" component="h3" sx={{ m: 0, fontWeight: 500 }}>{title} ({items.length})</Typography>
            <List disablePadding dense={dense}>
              {items.map((item, index) => {
                const content = <Stack spacing={0.5} sx={{ py: dense ? 0.5 : 1, minWidth: 0 }}>
                  <StatusBadge status={status} label={item.message} size="sm" />
                  {item.path && <Typography variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>{item.path}{item.line ? `:${item.line}` : ''}</Typography>}
                </Stack>;
                return <ListItem key={`${item.code}-${item.path || ''}-${item.line || ''}-${index}`} disablePadding>
                  {onSelect ? <ListItemButton onClick={() => onSelect(item)} sx={{ px: 1, minWidth: 0 }}>{content}</ListItemButton> : content}
                </ListItem>;
              })}
            </List>
          </Box>
        );
      })}
    </Stack>
  );
}
ChecksList.propTypes = { checks: PropTypes.shape({ items: PropTypes.arrayOf(PropTypes.object), summary: PropTypes.object }), onSelect: PropTypes.func, dense: PropTypes.bool };
