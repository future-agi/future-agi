import PropTypes from "prop-types";
import { useRef } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { PlanLine, PlanRow, Quiet } from "./planParts";
import ReadingLine from "./ReadingLine";

const ICON = {
  done: ["solar:check-circle-linear", "status.success.fg"],
  queued: ["solar:clock-circle-linear", "text.disabled"],
  failed: ["solar:danger-circle-linear", "status.danger.fg"],
  cancelled: ["solar:close-circle-linear", "text.disabled"],
};

const StateIcon = ({ state }) => (
  <Iconify
    icon={ICON[state][0]}
    width={14}
    sx={{ color: ICON[state][1], flexShrink: 0 }}
  />
);
StateIcon.propTypes = { state: PropTypes.oneOf(Object.keys(ICON)) };

const quietBtnSx = {
  color: "text.primary",
  borderColor: "divider",
  bgcolor: "background.paper",
  flexShrink: 0,
};

/**
 * The approved design's build: its stages as they run, then its checks, then
 * the release it saved, or the check that failed and what to do about it.
 * Saving runs nothing; connecting an agent is the user's next step.
 */
export default function DesignBuildView({
  build,
  onLeaveOut,
  timeout,
  outcome = true,
}) {
  // The last build, so its words stay while the view collapses away.
  const last = useRef(build);
  if (build) last.current = build;
  if (!last.current) return null;
  const { status, stages, validation, release, failure } = last.current;
  const done = stages.filter((s) => s.status === "done").length;
  const failed = validation?.checks.find((c) => !c.ok);
  // What went wrong, the file to leave out for it, and the banner's look.
  const problem =
    status === "failed"
      ? {
          text: `Building ${failure.stage.toLowerCase()} failed, so nothing was saved and the draft is kept. ${failure.detail} Leave it out (add a corrected copy if you have one), then approve again.`,
          file: failure.file,
          look: "danger",
        }
      : status === "cancelled"
        ? {
            text: "The build was cancelled, so nothing was saved and the draft is kept. Approve again to build it.",
            look: "neutral",
          }
        : failed && {
            text: `A check failed, so nothing was saved and the draft is kept. ${failed.file ? `Leave out ${failed.file.name} (add a corrected copy if you have one), then approve again.` : "Fix the design, then approve again."}`,
            look: "warning",
          };
  const [border, bg, icon, color] = {
    danger: [
      "status.danger.border",
      "status.danger.bg",
      "solar:danger-circle-linear",
      "status.danger.fg",
    ],
    warning: [
      "status.warning.border",
      "status.warning.bg",
      "solar:danger-triangle-linear",
      "status.warning.fg",
    ],
    neutral: [
      "divider",
      "background.paper",
      "solar:close-circle-linear",
      "text.secondary",
    ],
  }[problem?.look] || [
    "status.success.border",
    "status.success.bg",
    "solar:check-circle-linear",
    "status.success.fg",
  ];

  return (
    <Box>
      <Box sx={{ pt: 3 }}>
        <PlanRow label="Build" count={`${done} of ${stages.length}`}>
          {stages.map((s) =>
            s.status === "running" ? (
              <ReadingLine
                key={s.id}
                text={`Building ${s.label.toLowerCase()}…`}
              />
            ) : (
              <PlanLine key={s.id} empty={s.status === "queued"}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <StateIcon state={s.status} />
                  <span>{s.label}</span>
                </Stack>
              </PlanLine>
            ),
          )}
        </PlanRow>
      </Box>

      <Collapse
        in={
          status !== "building" && status !== "failed" && status !== "cancelled"
        }
        timeout={timeout}
        unmountOnExit
      >
        <Box sx={{ pt: 3 }}>
          <PlanRow
            label="Checks"
            count={
              validation
                ? `${validation.checks.filter((c) => c.ok).length} of ${validation.checks.length} passed`
                : ""
            }
          >
            {validation ? (
              validation.checks.map((c) => (
                <PlanLine key={c.label}>
                  <Stack
                    direction="row"
                    alignItems="center"
                    flexWrap="wrap"
                    useFlexGap
                    columnGap={1}
                    rowGap={0.75}
                    sx={{ py: 0.75 }}
                  >
                    <StateIcon state={c.ok ? "done" : "failed"} />
                    {/* Wraps in full; the button drops below when the row is narrow. */}
                    <Box
                      sx={{
                        flex: "1 1 240px",
                        minWidth: 0,
                        whiteSpace: "normal",
                      }}
                    >
                      {c.label}
                      {c.detail && <Quiet>{c.detail}</Quiet>}
                      {c.affects?.length > 0 && (
                        <Box
                          sx={{
                            mt: 0.75,
                            typography: "s2",
                            color: "text.secondary",
                          }}
                        >
                          {`Scenarios it affects (${c.affects.length}):`}
                          <Box component="ul" sx={{ m: 0, pl: 2.5 }}>
                            {c.affects.map((text) => (
                              <li key={text}>{text}</li>
                            ))}
                          </Box>
                        </Box>
                      )}
                    </Box>
                    {c.file && (
                      <Button
                        size="small"
                        variant="outlined"
                        onClick={() => onLeaveOut(c.file.id)}
                        sx={quietBtnSx}
                      >
                        {`Leave out ${c.file.name}`}
                      </Button>
                    )}
                  </Stack>
                </PlanLine>
              ))
            ) : (
              <ReadingLine text="Checking the build…" />
            )}
          </PlanRow>
        </Box>
      </Collapse>

      <Collapse
        in={outcome && (status === "saved" || !!problem)}
        timeout={timeout}
        unmountOnExit
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={1.25}
          sx={{
            mt: 3,
            px: 1.5,
            py: 1,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: border,
            bgcolor: bg,
          }}
        >
          <Iconify icon={icon} width={16} sx={{ color, flexShrink: 0 }} />
          <Typography sx={{ typography: "s1", color: "text.primary", flex: 1 }}>
            {problem
              ? problem.text
              : `Saved as v${release?.version}. Nothing runs until you connect an agent and start a run.`}
          </Typography>
          {problem?.file && (
            <Button
              size="small"
              variant="outlined"
              onClick={() => onLeaveOut(problem.file.id)}
              sx={quietBtnSx}
            >
              {`Leave out ${problem.file.name}`}
            </Button>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
DesignBuildView.propTypes = {
  // useDesignBuild(draftId); null before approval.
  build: PropTypes.object,
  // (fileId) to leave a file out after a failed check.
  onLeaveOut: PropTypes.func.isRequired,
  timeout: PropTypes.number,
  // false leaves the closing banner to the page around it.
  outcome: PropTypes.bool,
};
