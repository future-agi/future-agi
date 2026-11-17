import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { isExpiredSource } from "src/sections/develop-detail/TaskSuite/itemModel";
import {
  isRefusedSourceRead,
  useSourceSessionTraces,
} from "src/api/develop/row-source";
import { expiredCopyText } from "./RowViewStates";
import SavedRowCopy from "./SavedRowCopy";

const turnText = (value) => {
  if (value == null || value === "") return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const TurnSide = ({ who, text }) =>
  text ? (
    <Box sx={{ minWidth: 0 }}>
      <Typography typography="s3" color="text.secondary">
        {who}
      </Typography>
      <Typography
        typography="s2"
        component="div"
        sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
      >
        {text}
      </Typography>
    </Box>
  ) : null;
TurnSide.propTypes = { who: PropTypes.string, text: PropTypes.string };

/** Live session turns, paginated by the scoped API; expired sources use the saved row. */
const SessionConversation = ({ datasetId, row, source }) => {
  const expired = isExpiredSource(source);
  const sessionQ = useSourceSessionTraces(source?.session_id, {
    enabled: !expired,
    projectId: source?.project_id,
  });
  const gone =
    expired ||
    !source?.session_id ||
    (sessionQ.isError && isRefusedSourceRead(sessionQ.error));
  const turns = gone ? [] : sessionQ.data ?? [];
  if (!gone && sessionQ.isLoading) {
    return (
      <Box sx={{ p: 3 }}>
        <Typography typography="s2" color="text.secondary">
          Loading the session…
        </Typography>
      </Box>
    );
  }
  const savedOnly = gone || !turns.length;
  return (
    <Stack gap={1.5} sx={{ px: 2, pt: 1.5, pb: 3 }}>
      {savedOnly ? (
        <>
          <Stack
            direction="row"
            alignItems="center"
            gap={0.75}
            data-testid="conversation-expired-note"
            sx={{
              px: 1.25,
              py: 0.75,
              borderRadius: 1,
              bgcolor: (t) => alpha(t.palette.amber[500], 0.12),
            }}
          >
            <Iconify
              icon="mdi:history"
              width={16}
              sx={{ color: "amber.700", flexShrink: 0 }}
            />
            <Typography typography="s2">
              {gone
                ? expiredCopyText(source)
                : "The session could not be read right now; showing the copy saved at import."}
            </Typography>
          </Stack>
          <SavedRowCopy datasetId={datasetId} row={row} />
        </>
      ) : (
        <>
          {turns.map((turn, i) => (
            <Stack
              key={turn.trace_id ?? i}
              gap={0.75}
              data-testid="session-turn"
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                px: 1.5,
                py: 1.25,
              }}
            >
              <Typography typography="s2" fontWeight={600}>
                Turn {i + 1}
              </Typography>
              <TurnSide who="User" text={turnText(turn.input)} />
              <TurnSide who="Agent" text={turnText(turn.output)} />
            </Stack>
          ))}
          {sessionQ.isFetchNextPageError && (
            <Typography role="alert" typography="s2" color="text.secondary">
              More turns could not be loaded. The turns already loaded are kept.
            </Typography>
          )}
          {(sessionQ.hasNextPage || sessionQ.isFetchNextPageError) && (
            <Button
              onClick={() => sessionQ.fetchNextPage()}
              disabled={sessionQ.isFetchingNextPage}
            >
              {sessionQ.isFetchingNextPage
                ? "Loading turns…"
                : sessionQ.isFetchNextPageError
                  ? "Retry loading turns"
                  : "Load more turns"}
            </Button>
          )}
        </>
      )}
    </Stack>
  );
};
SessionConversation.propTypes = {
  datasetId: PropTypes.string,
  row: PropTypes.object,
  source: PropTypes.object,
};
export default SessionConversation;
