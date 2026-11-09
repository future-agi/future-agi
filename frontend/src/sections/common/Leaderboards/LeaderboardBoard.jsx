// the interface contract (the interface contract §7 C2): one leaderboard: its rule, its ranked runs, Edit,
// Copy link (opens this board in this browser), Delete. Rows are read again
// on every visit, so new matching runs appear. (L7c g1, the capability contract a: no
// Copy as API call: the real API serves no leaderboard route.)
import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";
import { useTags } from "src/sections/common/Tags/useTags";
import LeaderboardEditor from "./LeaderboardEditor";
import LeaderboardTable from "./LeaderboardTable";
import { shareLinkOf } from "src/sections/experiment-detail/results/shareLink";
import { leaderboardHref, leftOutText } from "./leaderboardModel";
import {
  useDeleteLeaderboard,
  useLeaderboard,
  useUpdateLeaderboard,
} from "./useLeaderboards";

/** The share button's label: boards live in this browser (prototype). */
const BOARD_LINK_LABEL = "Copy link (opens this board in this browser)";

export default function LeaderboardBoard({ id }) {
  const { data: board, isLoading, isError } = useLeaderboard(id);
  const { data: reg } = useTags();
  const tagsByName = useMemo(
    () => Object.fromEntries((reg?.tags ?? []).map((t) => [t.name, t])),
    [reg],
  );
  const [editOpen, setEditOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const update = useUpdateLeaderboard();
  const remove = useDeleteLeaderboard();
  const navigate = useNavigate();

  if (isLoading)
    return <Typography color="text.secondary">Loading leaderboard…</Typography>;
  if (isError || !board)
    return (
      <Stack gap={1}>
        <Typography variant="subtitle1">
          This leaderboard does not exist
        </Typography>
        <Link component={RouterLink} to="/dashboard/develop/leaderboards">
          All leaderboards
        </Link>
      </Stack>
    );

  const def = board.definition;
  const sortCol = board.columns.find((c) => c.key === def.sort?.column);
  const leftOut = leftOutText(board.left_out, sortCol?.label);
  const share = () => {
    copyToClipboard(
      shareLinkOf(`${window.location.origin}${leaderboardHref(def.id)}`),
    );
    enqueueSnackbar("Link copied. It opens this board in this browser.", {
      variant: "success",
    });
  };

  return (
    <Stack gap={2} data-testid="leaderboard-board" data-board-id={def.id}>
      <Link
        component={RouterLink}
        to="/dashboard/develop/leaderboards"
        variant="body2"
        underline="hover"
      >
        ← All leaderboards
      </Link>
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        gap={2}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="h5">{def.name}</Typography>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            flexWrap="wrap"
            sx={{ mt: 0.75 }}
          >
            {(board.datasets ?? []).map((d) => (
              <Chip key={d.id} size="small" label={d.name} variant="outlined" />
            ))}
            <Typography variant="body2" color="text.secondary">
              Runs where
            </Typography>
            <Typography
              variant="body2"
              data-testid="leaderboard-rule"
              sx={{
                fontFamily: "monospace",
                bgcolor: "action.hover",
                px: 0.75,
                borderRadius: 0.5,
              }}
            >
              {def.run_filter || "every finished run"}
            </Typography>
          </Stack>
        </Box>
        <Stack direction="row" gap={1} flexShrink={0}>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setEditOpen(true)}
          >
            Edit
          </Button>
          <Button
            size="small"
            variant="outlined"
            onClick={share}
            startIcon={<Iconify icon="mdi:link-variant" width={16} />}
            data-testid="leaderboard-copy-link"
          >
            {BOARD_LINK_LABEL}
          </Button>
          <Button
            size="small"
            color="error"
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </Button>
        </Stack>
      </Stack>
      {board.filter_error && (
        <Alert severity="error">{`The rule no longer reads: ${board.filter_error}. Edit it to show runs again.`}</Alert>
      )}
      <Typography
        variant="body2"
        color="text.secondary"
        data-testid="leaderboard-counts"
      >
        {`${board.rows.length} ranked · ${board.matched} matching run${board.matched === 1 ? "" : "s"}`}
        {leftOut ? ` · ${leftOut}` : ""}
      </Typography>
      <LeaderboardTable
        board={board}
        tagsByName={tagsByName}
        onSort={(sort) => update.mutate({ id: def.id, sort })}
      />
      <LeaderboardEditor
        open={editOpen}
        onClose={() => setEditOpen(false)}
        initial={def}
      />
      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)}>
        <DialogTitle>{`Delete “${def.name}”?`}</DialogTitle>
        <DialogContent>
          <Typography variant="body2">
            The board and its link go away. Runs, tags and datasets stay as they
            are.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDelete(false)}>Cancel</Button>
          <Button
            color="error"
            variant="contained"
            onClick={async () => {
              await remove.mutateAsync(def.id);
              setConfirmDelete(false);
              navigate("/dashboard/develop/leaderboards");
            }}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}

LeaderboardBoard.propTypes = { id: PropTypes.string.isRequired };
