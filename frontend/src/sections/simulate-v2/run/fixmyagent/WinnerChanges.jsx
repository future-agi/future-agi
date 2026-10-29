import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { changeFileFor } from "../../_mock/optimize";
import { CodePlan } from "./ImprovementPanels";

/* What the winner changes — each file, why, and the lines. */
export default function WinnerChanges({ env, winner, members, record }) {
  const proposals = winner?.proposals || [];
  if (!proposals.length) {
    return (
      <Typography
        sx={{ px: 2, py: 2.5, typography: "s2", color: "text.tertiary" }}
      >
        No change to show — no run qualified.
      </Typography>
    );
  }
  return (
    <Stack
      divider={
        <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
      }
    >
      {/* A code or structural change says its plan before its diff. */}
      <CodePlan env={env} record={record} proposals={proposals} />
      {proposals.map((p) => (
        <Box key={p.id} sx={{ px: 2, py: 1.5 }}>
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ mb: 0.5 }}
          >
            <Typography
              sx={{
                typography: "s3",
                fontFamily: "ui-monospace, Menlo, monospace",
                color: "text.secondary",
              }}
            >
              {changeFileFor(env, p)?.path || p.kind}
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary" }}
            >{`· ${p.kind}`}</Typography>
            {members?.[p.id] && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {members[p.id].member
                  ? `· changes ${members[p.id].member.name}`
                  : "· shared by every member"}
              </Typography>
            )}
          </Stack>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {p.title}
          </Typography>
          {p.why && (
            <Typography
              sx={{ typography: "s2", color: "text.secondary", mt: 0.25 }}
            >{`Why: ${p.why}`}</Typography>
          )}
          {p.editedByYou && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              Edited by you before it ran.
            </Typography>
          )}
          {/* A skill change: the version it makes, and whether its use was seen. */}
          {p.kind === "Skill" && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mt: 0.25 }}
            >
              {`${p.skillId} v${p.skillFrom} → v${p.skillTo} · ${
                p.observed
                  ? "skill use observed from the runtime's loading events"
                  : "skill use unknown: this runtime doesn't report loading, so the result shows outcomes only"
              }`}
            </Typography>
          )}
          {/* A settings change is a typed edit within the platform's allowed values. */}
          {p.setting && (
            <Typography
              sx={{
                typography: "s3",
                color: p.setting.inRange ? "text.tertiary" : "status.danger.fg",
                mt: 0.25,
              }}
            >
              {`${p.setting.label}: ${p.setting.value} · allowed ${p.setting.range}${p.setting.inRange ? "" : " — out of range, rejected at Static checks"}`}
            </Typography>
          )}
          {!!p.diff?.length && (
            <Stack spacing={0.25} sx={{ mt: 1 }}>
              {p.diff.map((d, i) => (
                <Stack
                  key={`${d.type}-${i}`}
                  direction="row"
                  spacing={1}
                  sx={{
                    px: 1.25,
                    py: 0.5,
                    borderRadius: "4px",
                    bgcolor: (th) =>
                      d.type === "add"
                        ? th.palette.status.success.bg
                        : d.type === "remove"
                          ? th.palette.status.danger.bg
                          : "transparent",
                  }}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontWeight: 600,
                      width: 10,
                      flexShrink: 0,
                      color:
                        d.type === "add"
                          ? "status.success.fg"
                          : d.type === "remove"
                            ? "status.danger.fg"
                            : "text.disabled",
                    }}
                  >
                    {d.type === "add" ? "+" : d.type === "remove" ? "−" : " "}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: "ui-monospace, Menlo, monospace",
                      whiteSpace: "pre-wrap",
                      textDecoration:
                        d.type === "remove" ? "line-through" : "none",
                      color:
                        d.type === "remove" ? "text.secondary" : "text.primary",
                    }}
                  >
                    {d.text}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          )}
        </Box>
      ))}
    </Stack>
  );
}
WinnerChanges.propTypes = {
  env: PropTypes.object,
  winner: PropTypes.object,
  members: PropTypes.object,
  record: PropTypes.object,
};
