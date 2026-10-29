import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { handoffsOf } from "../_mock/handoffs";

const KIND = {
  receive: { icon: "solar:user-linear", color: "text.secondary" },
  delegate: { icon: "solar:arrow-right-linear", color: "text.primary" },
  tool: { icon: "solar:code-linear", color: "text.secondary" },
  return: { icon: "solar:arrow-left-linear", color: "text.primary" },
  timeout: { icon: "solar:clock-circle-linear", color: "status.danger.fg" },
  reply: { icon: "solar:chat-round-line-linear", color: "text.secondary" },
};

/**
 * A multi-agent conversation's handoffs, in order: the coordinator handing a
 * request to a worker, the worker's own tool calls, its return — or the
 * timeout when it never came back. Unobserved, the path is shown as declared
 * and its per-worker results as unmeasured. In a conversation's drawer.
 */
export default function HandoffTimeline({ agent, task }) {
  const h = handoffsOf({ agent, task });
  if (!h) return null;
  const observed = h.evidence.observed;
  return (
    <Stack spacing={1.5} sx={{ p: 2 }}>
      <Stack direction="row" spacing={1}>
        <Iconify
          icon={observed ? "solar:eye-linear" : "solar:eye-closed-linear"}
          width={16}
          sx={{ color: "text.secondary", mt: "2px", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {observed
            ? "Observed — from the delegation and return events this run recorded, under the same run ID."
            : h.evidence.line}
        </Typography>
      </Stack>

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          "& > * + *": { borderTop: "1px solid", borderColor: "divider" },
          ...(!observed && { opacity: 0.6 }),
        }}
      >
        {h.events.map((e, i) => {
          const k = KIND[e.kind];
          return (
            <Stack
              key={`${e.kind}-${i}`}
              direction="row"
              spacing={1.25}
              sx={{ px: 1.5, py: 1 }}
            >
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  width: 44,
                  flexShrink: 0,
                  fontVariantNumeric: "tabular-nums",
                  pt: "1px",
                }}
              >
                {`${e.at.toFixed(1)} s`}
              </Typography>
              <Iconify
                icon={k.icon}
                width={14}
                sx={{ color: k.color, mt: "3px", flexShrink: 0 }}
              />
              <Typography
                sx={{
                  typography: "s2",
                  color:
                    e.kind === "timeout" ? "status.danger.fg" : "text.primary",
                }}
              >
                {observed ? e.text : `${e.text} (declared, not observed)`}
              </Typography>
            </Stack>
          );
        })}
      </Box>

      {h.outcome === "timeout" && (
        <Box
          sx={{
            px: 1.5,
            py: 1.25,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${h.failedWorker.name} didn't finish`}
          </Typography>
          <Typography
            sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
          >
            A worker failure, not an infrastructure one — the world kept no
            change from it. Fix the app or its mapping, reset the world, then
            submit a fresh run against the same scenarios. This run&apos;s
            evidence stays.
          </Typography>
        </Box>
      )}
    </Stack>
  );
}

HandoffTimeline.propTypes = {
  agent: PropTypes.object,
  task: PropTypes.object.isRequired,
};
