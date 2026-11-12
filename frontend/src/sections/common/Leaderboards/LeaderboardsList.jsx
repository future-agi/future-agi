// W5-5 (W5-SCOPE §7 C2): every leaderboard in the workspace.
import React, { useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Box,
  Button,
  Chip,
  Link,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { fToNowStrict } from "src/utils/format-time";
import LeaderboardEditor from "./LeaderboardEditor";
import { leaderboardHref } from "./leaderboardModel";
import { useLeaderboardsList } from "./useLeaderboards";

export default function LeaderboardsList() {
  const { data: boards = [], isLoading } = useLeaderboardsList();
  const [editorOpen, setEditorOpen] = useState(false);
  const navigate = useNavigate();
  return (
    <Stack gap={2} data-testid="leaderboards-list">
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Box>
          <Typography variant="h5">Leaderboards</Typography>
          <Typography variant="body2" color="text.secondary">
            Rank runs from several datasets by the evals you pick. A board keeps
            a rule, not a list: every run that matches it shows up.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<Iconify icon="mdi:plus" width={18} />}
          onClick={() => setEditorOpen(true)}
        >
          New leaderboard
        </Button>
      </Stack>
      {!isLoading && !boards.length ? (
        <Box
          sx={{
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            p: 4,
            textAlign: "center",
          }}
        >
          <Typography variant="subtitle1">No leaderboard yet</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Pick datasets, a rule such as tags contains &quot;candidate&quot;,
            and the evals to rank by.
          </Typography>
          <Button variant="outlined" onClick={() => setEditorOpen(true)}>
            New leaderboard
          </Button>
        </Box>
      ) : (
        <Box
          sx={{
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            overflowX: "auto",
          }}
        >
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Datasets</TableCell>
                <TableCell>Runs where</TableCell>
                <TableCell align="right">Runs</TableCell>
                <TableCell>Created</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {boards.map((b) => (
                <TableRow
                  key={b.id}
                  hover
                  data-testid="leaderboards-list-row"
                  data-board-id={b.id}
                >
                  <TableCell>
                    <Link
                      component={RouterLink}
                      to={leaderboardHref(b.id)}
                      underline="hover"
                    >
                      {b.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Stack direction="row" gap={0.5} flexWrap="wrap">
                      {(b.datasets ?? []).map((d) => (
                        <Chip key={d.id} size="small" label={d.name} />
                      ))}
                    </Stack>
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="body2"
                      sx={{ fontFamily: "monospace" }}
                    >
                      {b.run_filter || "every finished run"}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">{b.row_count}</TableCell>
                  <TableCell>
                    <Typography variant="body2" color="text.secondary">
                      {[
                        b.created_by,
                        b.created_at ? fToNowStrict(b.created_at) : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      )}
      <LeaderboardEditor
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSaved={(read) => {
          const id = read?.definition?.id;
          if (id) navigate(leaderboardHref(id));
        }}
      />
    </Stack>
  );
}
