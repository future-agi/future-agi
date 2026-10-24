import ErrorState from "src/components/error-state";
import React from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { format, parseISO } from "date-fns";
import axios, { endpoints } from "src/utils/axios";
import EvalStatusIndicator from "src/components/eval/EvalStatusIndicator";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { getEvalNonScoreStatus } from "src/utils/evalStatus";

const ScoreChip = ({ value }) => {
  if (value == null) {
    return (
      <Typography variant="caption" color="text.disabled">
        —
      </Typography>
    );
  }
  return (
    <Typography variant="body2" sx={{ fontVariantNumeric: "tabular-nums" }}>
      {typeof value === "number" ? value.toFixed(2) : String(value)}
    </Typography>
  );
};
ScoreChip.propTypes = { value: PropTypes.any };

const StatusChip = ({ status, result, evalStatus, skippedReason }) => {
  // Not-yet-completed evals (pending/running/skipped) render a loading /
  // queued / skipped indicator instead of a terminal result chip.
  const nonScore = getEvalNonScoreStatus(evalStatus || status);
  if (nonScore) {
    return (
      <EvalStatusIndicator status={nonScore} skippedReason={skippedReason} />
    );
  }
  if (status === "error" || evalStatus === "errored") {
    return <EvalStatusIndicator status="errored" />;
  }
  if (result === "Passed") {
    return <StatusBadge status="passed" label="Passed" />;
  }
  if (result === "Failed") {
    return <StatusBadge status="failed" label="Failed" />;
  }
  return result ? (
    <Typography variant="body2" color="text.secondary">
      {result}
    </Typography>
  ) : (
    <Typography variant="body2" color="text.disabled">
      —
    </Typography>
  );
};
StatusChip.propTypes = {
  status: PropTypes.string,
  result: PropTypes.string,
  evalStatus: PropTypes.string,
  skippedReason: PropTypes.string,
};

const Row = ({ item }) => {
  const theme = useTheme();
  const created = item.created_at
    ? format(parseISO(item.created_at), "dd/MM/yyyy HH:mm")
    : "—";
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "1.5fr 1fr 1fr 2fr 1fr",
        alignItems: "center",
        gap: 1.5,
        px: 1.5,
        py: 1.25,
        borderBottom: `1px solid ${theme.palette.divider}`,
      }}
    >
      <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
        {item.eval_name || "—"}
      </Typography>
      <ScoreChip value={item.score} />
      <StatusChip
        status={item.status}
        result={item.result}
        evalStatus={item.eval_status}
        skippedReason={item.skipped_reason || item.reason}
      />
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
        title={item.reason}
      >
        {item.reason || "—"}
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {created}
      </Typography>
    </Box>
  );
};
Row.propTypes = { item: PropTypes.object.isRequired };

const SessionEvalsList = ({ sessionId }) => {
  const theme = useTheme();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["sessionEvalLogs", sessionId],
    queryFn: () =>
      axios
        // page is 0-indexed on the backend (offset = page * page_size); the
        // response shape is { total, page, page_size, items }.
        .get(endpoints.project.getSessionEvalLogs(sessionId), {
          params: { page: 0, page_size: 100 },
        })
        .then((res) => res.data?.result || { items: [], total: 0 }),
    enabled: Boolean(sessionId),
  });

  if (isLoading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={20} />
      </Box>
    );
  }

  if (isError) {
    return (
      <ErrorState
        title="Failed to load session evals"
        detail={error?.message || "unknown error"}
        sx={{ height: "auto", flexGrow: 0 }}
      />
    );
  }

  const items = data?.items || [];

  if (items.length === 0) {
    return (
      <Stack alignItems="center" spacing={1} sx={{ py: 4, px: 2 }}>
        <Typography variant="body2" color="text.secondary">
          No session-level evaluations yet.
        </Typography>
        <Typography variant="caption" color="text.secondary" textAlign="center">
          Configure a task with row type{" "}
          <Box component="span" sx={{ fontWeight: 600 }}>
            Sessions
          </Box>{" "}
          to evaluate this session.
        </Typography>
      </Stack>
    );
  }

  return (
    <Box>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "1.5fr 1fr 1fr 2fr 1fr",
          gap: 1.5,
          px: 1.5,
          py: 1,
          backgroundColor: theme.palette.background.default,
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Eval
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Score
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Result
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          Reason
        </Typography>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>
          When
        </Typography>
      </Box>
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
    </Box>
  );
};

SessionEvalsList.propTypes = {
  sessionId: PropTypes.string,
};

export default SessionEvalsList;
