import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Drawer,
  IconButton,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import Section from "src/components/section";
import EmptyState from "src/components/empty-state";
import StatusBadge from "src/components/status-badge";
import { DataTablePagination } from "src/components/data-table";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { IdentityValue, ReviewState } from "../review/ReleaseReviewBody";

const stageNames = {
  desired: "Desired",
  resolved: "Resolved",
  installed: "Installed",
  discovered: "Discovered",
  loaded: "Delivered",
};
const stateNames = {
  matches_target: "Matches target",
  pinned_as_intended: "Pinned as intended",
  update_available: "Update available",
  local_modifications: "Local modifications",
  mixed_sessions: "Mixed sessions",
  old_thread_pinned: "Old thread retained",
  stale_observation: "Stale observation",
  unknown: "Runtime loading not observed",
  quarantined: "Quarantined",
};
function Stage({ stage }) {
  return stage ? (
    <Stack spacing={0.25}>
      <Typography variant="s1" sx={{ overflowWrap: "anywhere" }}>
        {stage.versionId}
      </Typography>
      <Typography variant="s3">{stage.observedAt ?? "Time unknown"}</Typography>
      <Typography variant="s3">
        {stage.origin ?? "Provenance unknown"}
      </Typography>
    </Stack>
  ) : (
    <Typography variant="s3">Not observed</Typography>
  );
}
Stage.propTypes = { stage: PropTypes.object };
function ConsumerRows({ data, windowDays, onWindowChange, onPageChange }) {
  const [selectedId, setSelectedId] = useState(null);
  const selected = data.results.find((item) => item.binding.id === selectedId);
  return (
    <Stack spacing={1.5}>
      <Alert severity="info">
        Coverage: declared bindings only, as of {data.asOf}. An install or a
        fetch does not prove delivery. Offline and unregistered consumers remain
        unknown.
      </Alert>
      <TextField
        select
        size="small"
        label="Observation window"
        value={windowDays}
        onChange={(event) => onWindowChange(Number(event.target.value))}
      >
        {[7, 30, 90].map((days) => (
          <MenuItem key={days} value={days}>
            {days} days
          </MenuItem>
        ))}
      </TextField>
      {!data.results.length ? (
        <EmptyState
          variant="no-results"
          title="No declared consumers"
          description="Register a supported repository or runtime binding explicitly. Fetch activity is not a consumer inventory."
        />
      ) : (
        <>
          <TableContainer sx={{ maxWidth: 1 }}>
            <Table size="small" aria-label="Context delivery observations">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Binding</TableCell>
                  {Object.values(stageNames).map((name) => (
                    <TableCell key={name}>{name}</TableCell>
                  ))}
                  <TableCell>Observations</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.results.map((row) => (
                  <TableRow
                    key={row.binding.id}
                    hover
                    sx={{ height: ROW_HEIGHT.default }}
                  >
                    <TableCell>
                      <Button
                        color="inherit"
                        onClick={() => setSelectedId(row.binding.id)}
                      >
                        {row.binding.name}
                      </Button>
                      <Typography variant="s3">
                        {row.binding.selector.version
                          ? `Pinned v${row.binding.selector.version}`
                          : `Follow ${row.binding.selector.label}`}
                      </Typography>
                    </TableCell>
                    {Object.keys(stageNames).map((stage) => (
                      <TableCell
                        key={stage}
                        sx={{ minWidth: "18ch", maxWidth: "24ch" }}
                      >
                        <Stage stage={row[stage]} />
                      </TableCell>
                    ))}
                    <TableCell>
                      <Stack spacing={0.5}>
                        {row.states.map((state) => (
                          <StatusBadge
                            key={state}
                            status={state}
                            label={stateNames[state] ?? state}
                          />
                        ))}
                        <Typography variant="s3">
                          Age{" "}
                          {row.observationAgeSeconds === null
                            ? "unknown"
                            : `${row.observationAgeSeconds}s`}
                        </Typography>
                      </Stack>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
          <DataTablePagination
            page={data.page - 1}
            pageSize={data.pageSize}
            total={data.count}
            onPageChange={(page) => onPageChange(page + 1)}
            pageSizeOptions={[data.pageSize]}
          />
        </>
      )}
      <Drawer
        anchor="right"
        open={Boolean(selected)}
        onClose={() => setSelectedId(null)}
        PaperProps={{ sx: { width: { xs: 1, sm: 560 }, maxWidth: 1 } }}
      >
        {selected && (
          <Box sx={{ p: 2, minWidth: 0 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="center"
            >
              <Typography variant="m1">Delivery receipts</Typography>
              <IconButton
                aria-label="Close delivery receipts"
                onClick={() => setSelectedId(null)}
              >
                <Iconify icon="solar:close-linear" width={18} />
              </IconButton>
            </Stack>
            <Stack spacing={2}>
              <IdentityValue
                label="Binding"
                value={`${selected.binding.name} · ${selected.binding.id}`}
              />
              {Object.entries(stageNames).map(([key, label]) => (
                <Section key={key} title={label}>
                  <Stage stage={selected[key]} />
                  {selected[key] && (
                    <IdentityValue
                      label="Digest"
                      value={selected[key].digest}
                    />
                  )}
                </Section>
              ))}
              <IdentityValue
                label="Suggested next action"
                value={
                  selected.nextActionCode ?? "Inspect missing observations"
                }
              />
              <Typography variant="s3">
                Delivery is supplied content, not proven compliance or a
                successful business action. Old threads remain pinned; a new
                install does not erase older sessions. Version numbers do not
                establish freshness.
              </Typography>
            </Stack>
          </Box>
        )}
      </Drawer>
    </Stack>
  );
}
ConsumerRows.propTypes = {
  data: PropTypes.object.isRequired,
  windowDays: PropTypes.number.isRequired,
  onWindowChange: PropTypes.func.isRequired,
  onPageChange: PropTypes.func.isRequired,
};
export default function ContextConsumersView({
  scopeKey,
  state = "unavailable",
  data,
  windowDays = 7,
  onWindowChange,
  onPageChange,
  onRetry,
}) {
  return (
    <Section title="Consumers">
      {scopeKey && state === "ready" && data ? (
        <ConsumerRows
          key={scopeKey}
          {...{ data, windowDays, onWindowChange, onPageChange }}
        />
      ) : (
        <ReviewState
          state={!scopeKey ? "forbidden" : state}
          onRetry={onRetry}
        />
      )}
    </Section>
  );
}
ContextConsumersView.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  data: PropTypes.object,
  windowDays: PropTypes.number,
  onWindowChange: PropTypes.func,
  onPageChange: PropTypes.func,
  onRetry: PropTypes.func,
};
