import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { MONO_FONT as MONO } from "src/sections/simulate/environments/workspace/contract/contract.constants";
import {
  CALL_WORKERS_COPY as C,
  JSON_BUTTON_SX,
  MAX_ROW_INDENT,
} from "./callTraces.constants";
import {
  Answer,
  Payload,
  Reveal,
  Stamp,
  UnknownParent,
} from "./CallWorkersParts";

// One line of the raw list: the time, who, what kind of step, then its text or tool call.
function EventRow({ event, start, openJson, onOpenTrace, traceIds }) {
  const [open, setOpen] = useState(openJson);
  const call = event.call;
  const failed = call?.status === "error";
  return (
    <Box
      data-testid="raw-event"
      data-type={event.type}
      sx={{
        display: "grid",
        gridTemplateColumns: "44px minmax(0, 1fr)",
        columnGap: 1,
        py: 0.75,
        ml: Math.min(event.depth, MAX_ROW_INDENT) * 2.5,
        pl: event.depth ? 1.5 : 0,
        borderLeft: event.depth ? "1px solid" : 0,
        borderColor: "divider",
        // The browser skips laying out steps off screen, which keeps a long record quick to open and scroll.
        contentVisibility: "auto",
        containIntrinsicSize: "auto 72px",
      }}
    >
      <Stamp at={event.at} start={start} />
      <Stack spacing={0.5} sx={{ minWidth: 0, gridColumn: 2 }}>
        <Stack
          direction="row"
          useFlexGap
          alignItems="baseline"
          sx={{ flexWrap: "wrap", columnGap: 1 }}
        >
          <Typography sx={{ typography: "s1", fontWeight: 500 }}>
            {event.actor}
          </Typography>
          <Typography
            sx={{
              typography: "s3",
              color: event.bad ? "error.main" : "text.subtitle",
            }}
          >
            {event.type}
          </Typography>
          {event.to ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {`→ ${event.to}`}
            </Typography>
          ) : null}
          {event.parallel ? (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {C.inParallel}
            </Typography>
          ) : null}
          {event.unknown ? <UnknownParent /> : null}
          {/* Traces opens only a trace it lists. */}
          {onOpenTrace && traceIds.includes(event.traceId) ? (
            <Button
              size="small"
              variant="text"
              onClick={() => onOpenTrace(event.traceId, event.spanId)}
              sx={{ ...JSON_BUTTON_SX, ml: "auto" }}
            >
              {C.openInTraces}
            </Button>
          ) : null}
        </Stack>
        {call ? (
          <Stack spacing={0.75}>
            <Stack
              direction="row"
              useFlexGap
              alignItems="center"
              sx={{ flexWrap: "wrap", columnGap: 1, rowGap: 0.5 }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  fontFamily: MONO,
                  flexBasis: "100%",
                  minWidth: 0,
                  overflowWrap: "anywhere",
                  color: failed ? "error.main" : "text.primary",
                }}
              >
                {call.tool}
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: failed ? "error.main" : "text.secondary",
                }}
              >
                {failed ? C.failed : C.ok}
              </Typography>
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                aria-expanded={open}
                onClick={() => setOpen((one) => !one)}
                startIcon={<Iconify icon="solar:code-linear" width={12} />}
                sx={JSON_BUTTON_SX}
              >
                {open ? C.hideJson : C.showJson}
              </Button>
            </Stack>
            <Reveal open={open}>
              <Stack spacing={0.75}>
                <Payload
                  label={C.input}
                  value={call.input}
                  omitted={call.input_omitted}
                />
                <Payload
                  label={C.output}
                  value={call.output}
                  omitted={call.output_omitted}
                />
              </Stack>
            </Reveal>
          </Stack>
        ) : event.shown || event.empty ? (
          <Box
            sx={{ p: 1, borderRadius: "6px", bgcolor: "background.neutral" }}
          >
            {/* Worker text keeps the tab's folding and its block on images. */}
            <Answer shown={event.shown} empty={event.empty} />
          </Box>
        ) : event.note ? (
          <Typography
            sx={{
              typography: "s1",
              color: event.bad ? "error.main" : "text.secondary",
            }}
          >
            {event.note}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
}
EventRow.propTypes = {
  event: PropTypes.object.isRequired,
  start: PropTypes.string,
  openJson: PropTypes.bool,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};

export function EventList({
  events,
  start,
  openJson,
  bordered,
  onOpenTrace,
  traceIds,
}) {
  return (
    <Box
      data-testid="raw-events"
      sx={{
        ...(bordered && {
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          px: 1.5,
          py: 0.5,
        }),
        "& > * + *": { borderTop: "1px solid", borderTopColor: "divider" },
      }}
    >
      {events.map((event) => (
        <EventRow
          key={event.key}
          event={event}
          start={start}
          openJson={openJson(event)}
          onOpenTrace={onOpenTrace}
          traceIds={traceIds}
        />
      ))}
    </Box>
  );
}
EventList.propTypes = {
  events: PropTypes.array.isRequired,
  start: PropTypes.string,
  openJson: PropTypes.func.isRequired,
  bordered: PropTypes.bool,
  onOpenTrace: PropTypes.func,
  traceIds: PropTypes.arrayOf(PropTypes.string).isRequired,
};
