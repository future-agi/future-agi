import PropTypes from "prop-types";
import { Box, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { useCallWorkers } from "src/api/simulate-environments/runDetail";
import { CALL_WORKERS_COPY as C } from "./callTraces.constants";

/**
 * One call of a multi-agent app as a timeline under the coordinator: each hand-off to a
 * worker, that worker's tool calls, and what it returned. Read from the spans the app
 * exported and the sandbox's record of each tool call, so a declared worker that never ran
 * shows as not observed.
 */
export default function CallWorkersPanel({ callExecutionId }) {
  const { data, isLoading, isError } = useCallWorkers(callExecutionId);
  if (isLoading) return <CircularProgress size={20} sx={{ m: 2 }} />;
  if (isError)
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "error.main" }}>
        {C.error}
      </Typography>
    );
  if (!data)
    return (
      <Typography sx={{ p: 2, typography: "s2", color: "text.secondary" }}>
        {C.none}
      </Typography>
    );

  const names = Object.fromEntries(
    data.workers.map((one) => [one.worker_id, one.name]),
  );
  const nameOf = (id) => (id ? names[id] || id : C.coordinator);
  return (
    <Stack spacing={1.5} sx={{ p: 1.5 }}>
      <Stack direction="row" spacing={0.75} flexWrap="wrap" rowGap={0.75}>
        {data.workers.map((one) => (
          <Chip
            key={one.worker_id}
            size="small"
            variant="outlined"
            label={[
              one.name,
              !one.observed && C.notObserved,
              one.undeclared && C.undeclared,
            ]
              .filter(Boolean)
              .join(" · ")}
            color={one.observed ? "default" : "warning"}
          />
        ))}
      </Stack>
      <Stack spacing={0.5}>
        {data.events.map((event) => (
          <EventLine key={event.seq} event={event} nameOf={nameOf} />
        ))}
      </Stack>
    </Stack>
  );
}

CallWorkersPanel.propTypes = { callExecutionId: PropTypes.string };

function EventLine({ event, nameOf }) {
  const nested = event.type === "tool_call" && event.worker_id;
  const text =
    event.type === "delegated"
      ? `${C.delegated(nameOf(event.worker_id))}${event.task ? `: ${event.task}` : ""}`
      : event.type === "returned"
        ? C.returned(nameOf(event.worker_id))
        : `${event.attribution === "unattributed" ? C.unknown : nameOf(event.worker_id)} · ${event.tool}`;
  return (
    <Box sx={{ pl: nested ? 3 : 0 }}>
      <Typography
        sx={{
          typography: "s2",
          fontWeight: event.type === "tool_call" ? 400 : 600,
          color: event.status === "error" ? "error.main" : "text.primary",
          wordBreak: "break-word",
        }}
      >
        {text}
        {event.status === "error" ? ` (${C.failed})` : ""}
      </Typography>
      {event.type === "returned" && event.result ? (
        <Box
          sx={{
            pl: 3,
            typography: "s2",
            color: "text.secondary",
            wordBreak: "break-word",
            "& p, & ul, & ol": { my: 0.25 },
            "& ul, & ol": { pl: 2.5 },
          }}
        >
          {/* A worker's answer is often markdown; show it as the app's other agent text is shown. */}
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeSanitize]}
          >
            {event.result}
          </ReactMarkdown>
        </Box>
      ) : null}
    </Box>
  );
}

EventLine.propTypes = {
  event: PropTypes.object.isRequired,
  nameOf: PropTypes.func.isRequired,
};
