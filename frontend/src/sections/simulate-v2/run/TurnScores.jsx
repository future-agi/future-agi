import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
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
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>{d.label}</Typography>
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
              p: 1.25, borderRadius: 1,
              border: "1px solid",
              borderColor: t.weak ? (th) => alpha("#DC2626", 0.35) : "divider",
              bgcolor: (th) => t.weak
                ? alpha("#DC2626", th.palette.mode === "dark" ? 0.1 : 0.04)
                : "background.paper",
            }}
          >
            <Typography sx={{ width: 38, flexShrink: 0, typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>
              {t.at}
            </Typography>
            <Iconify
              icon={agent ? "solar:cpu-bolt-linear" : "solar:user-rounded-linear"}
              width={14}
              sx={{ color: "text.subtitle", flexShrink: 0, mt: "2px" }}
            />
            <Typography sx={{ flex: 1, minWidth: 0, typography: "s2", color: "text.secondary" }}>
              {t.text}
            </Typography>
            <Stack direction="row" spacing={0.5} sx={{ flexShrink: 0 }}>
              {Object.entries(t.scores).map(([k, v]) => {
                const bad = v < 0.6;
                const color = bad ? "#DC2626" : v < 0.9 ? "#CA8A04" : "#16A34A";
                return (
                  <Tooltip key={k} arrow title={`${k} ${Math.round(v * 100)}%`}>
                    <Typography
                      sx={{
                        px: 0.625, py: 0.125, borderRadius: 0.5,
                        typography: "s3", fontWeight: 700, color,
                        bgcolor: (th) => alpha(color, th.palette.mode === "dark" ? 0.16 : 0.1),
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
