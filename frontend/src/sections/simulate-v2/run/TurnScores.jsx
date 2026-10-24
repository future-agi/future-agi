import PropTypes from "prop-types";
import { Box, Stack, Typography, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";
import { turnScores, TURN_DIMENSIONS } from "../_mock/grading";

/**
 * Where in the conversation it went wrong.
 *
 * A journey score says the task failed. A turn score says which sentence did
 * it — here, the agent promising five working days before it had asked the
 * tool. That is the difference between a number to argue about and a line to
 * go and fix.
 */
export default function TurnScores({ task }) {
  const turns = turnScores(task);

  return (
    <Stack spacing={1.25} sx={{ p: 2 }}>
      <Stack direction="row" spacing={2} sx={{ flexWrap: "wrap", rowGap: 0.5 }}>
        {TURN_DIMENSIONS.map((d) => (
          <Tooltip key={d.id} arrow title={d.help}>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {d.label}
            </Typography>
          </Tooltip>
        ))}
      </Stack>

      {turns.map((t) => {
        const agent = t.speaker === "agent";
        return (
          <Stack
            key={t.index}
            direction="row"
            alignItems="flex-start"
            spacing={1.5}
            sx={{
              p: 1.25,
              borderRadius: 1,
              border: "1px solid",
              borderColor: t.weak ? "status.danger.border" : "divider",
              bgcolor: (th) =>
                t.weak ? th.palette.status.danger.bg : "background.paper",
            }}
          >
            <Typography
              sx={{
                width: 38,
                flexShrink: 0,
                typography: "s3",
                color: "text.tertiary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {t.at}
            </Typography>
            <Iconify
              icon={
                agent ? "solar:cpu-bolt-linear" : "solar:user-rounded-linear"
              }
              width={14}
              sx={{ color: "text.tertiary", flexShrink: 0, mt: "2px" }}
            />
            <Typography
              sx={{
                flex: 1,
                minWidth: 0,
                typography: "s2",
                color: "text.secondary",
              }}
            >
              {t.text}
            </Typography>
            <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
              {Object.entries(t.scores).map(([k, v]) => {
                const bad = v < 0.6;
                const role = bad ? "danger" : v < 0.9 ? "warning" : "success";
                const textColor =
                  !bad && v < 0.9 ? "text.primary" : `status.${role}.fg`;
                return (
                  <Tooltip key={k} arrow title={`${k} ${Math.round(v * 100)}%`}>
                    <Typography
                      sx={{
                        px: 0.625,
                        py: 0.125,
                        borderRadius: "4px",
                        typography: "s3",
                        fontWeight: 600,
                        color: textColor,
                        bgcolor: `status.${role}.bg`,
                      }}
                    >
                      {Math.round(v * 100)}
                    </Typography>
                  </Tooltip>
                );
              })}
            </Stack>
          </Stack>
        );
      })}
    </Stack>
  );
}

TurnScores.propTypes = { task: PropTypes.object };
