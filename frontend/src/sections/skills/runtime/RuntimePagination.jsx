import { useState } from 'react';
import PropTypes from 'prop-types';
import { Button, Stack, Typography } from '@mui/material';

/** Cursor history belongs to one resource/filter scope, never to the next one. */
export function useRuntimePagination(scope) {
  const identity = JSON.stringify(scope);
  const [state, setState] = useState({ identity, cursors: [undefined] });
  // Reset during render so returning to an earlier scope cannot resurrect a
  // cursor whose frozen time bounds have since changed.
  if (state.identity !== identity) setState({ identity, cursors: [undefined] });
  const cursors = state.identity === identity ? state.cursors : [undefined];
  return {
    cursor: cursors[cursors.length - 1],
    page: cursors.length,
    next: (cursor) => { if (cursor) setState({ identity, cursors: [...cursors, cursor] }); },
    previous: () => setState({ identity, cursors: cursors.length > 1 ? cursors.slice(0, -1) : cursors }),
  };
}

export default function RuntimePagination({ paging, query }) {
  const next = query.data?.nextCursor;
  if (paging.page === 1 && !next) return null;
  const busy = Boolean(query.isFetching || query.isPending);
  return (
    <Stack direction="row" spacing={1} alignItems="center" aria-label="Runtime pages">
      <Button disabled={busy || paging.page === 1} onClick={paging.previous}>Previous page</Button>
      <Typography variant="s3" role="status">Page {paging.page}</Typography>
      <Button disabled={busy || !next} onClick={() => paging.next(next)}>Next page</Button>
    </Stack>
  );
}
RuntimePagination.propTypes = { paging: PropTypes.object.isRequired, query: PropTypes.object.isRequired };
