import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, Button, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ConfirmDialog from "src/components/custom-dialog/confirm-dialog";
import ShimmerText from "../components/ShimmerText";
import usePrefersReducedMotion from "../components/usePrefersReducedMotion";
import UiOnlyFlag from "../components/UiOnlyFlag";
import DesignBuildView from "./DesignBuildView";

// The tab each build stage writes into; the release checks land on Overview.
const FOLLOW_TAB = {
  records: "world",
  tools: "world",
  checks: "world",
  scenarios: "scenarios",
};

// How the strip looks once the build has stopped: [border, background, icon, color].
const LOOK = {
  ready: [
    "status.success.border",
    "status.success.bg",
    "solar:check-circle-linear",
    "status.success.fg",
  ],
  failed: [
    "status.danger.border",
    "status.danger.bg",
    "solar:danger-circle-linear",
    "status.danger.fg",
  ],
  cancelled: [
    "divider",
    "background.paper",
    "solar:close-circle-linear",
    "text.secondary",
  ],
};
const KEPT = "Nothing was saved and your draft is kept.";

/**
 * An approved design's first build, above the environment's tabs: the stage
 * running (it can be cancelled), then its checks, then that it waits to be
 * reviewed and saved, or that it failed or was cancelled with the draft kept.
 * The tabs follow the build until a tab is picked. Gone once it's saved.
 */
export default function DesignBuildStrip({
  build,
  tab,
  onFollow,
  onCancel,
  onBack,
}) {
  const reduce = usePrefersReducedMotion();
  const timeout = reduce ? 0 : undefined;
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState(false);
  // The last build, so its words stay while the strip collapses away.
  const last = useRef(build);
  if (build) last.current = build;
  const shown = last.current;
  const running = shown?.stages.find((s) => s.status === "running");
  const working = ["building", "validating"].includes(shown?.status);
  const target = working
    ? running
      ? FOLLOW_TAB[running.id]
      : "overview"
    : null;

  // Following stops for good once someone picks another tab.
  const followed = useRef(null);
  const stopped = useRef(false);
  useEffect(() => {
    if (followed.current && tab !== followed.current) stopped.current = true;
  }, [tab]);
  useEffect(() => {
    if (!target || stopped.current || target === followed.current) return;
    followed.current = target;
    onFollow?.(target);
  }, [target, onFollow]);

  if (!shown) return null;
  const done = shown.stages.filter((s) => s.status === "done").length;
  const checks = shown.validation?.checks || [];
  const stoppedAt = shown.stages.find((s) => s.status === "cancelled");
  const line = {
    building: `Building ${running?.label.toLowerCase() || "the environment"} · ${done + 1} of ${shown.stages.length}`,
    validating: "Running the release checks",
    ready: `Built and checked: ${checks.filter((c) => c.ok).length} of ${checks.length} checks passed. Review the changes and save them as v1.`,
    failed: `Building ${shown.failure?.stage.toLowerCase()} failed: ${shown.failure?.detail} ${KEPT}`,
    cancelled: `Build cancelled ${stoppedAt ? `at ${stoppedAt.label.toLowerCase()}` : "during the release checks"}. ${KEPT}`,
  }[shown.status];
  const [border, bg, icon, color] = LOOK[shown.status] || LOOK.ready;
  const ended = shown.status === "failed" || shown.status === "cancelled";

  return (
    <Collapse in={build?.status !== "saved"} timeout={timeout} unmountOnExit>
      <Box sx={{ px: 2, pt: 1.5, flexShrink: 0 }}>
        <Box
          sx={{
            border: "1px solid",
            borderColor: working ? "divider" : border,
            bgcolor: working ? "background.paper" : bg,
            borderRadius: "8px",
            px: 2,
            py: 1,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            {working ? (
              <ShimmerText sx={{ flex: 1, minWidth: 0 }}>{line}</ShimmerText>
            ) : (
              <>
                <Iconify icon={icon} width={16} sx={{ color, flexShrink: 0 }} />
                <Typography sx={{ typography: "s1", flex: 1, minWidth: 0 }}>
                  {line}
                </Typography>
              </>
            )}
            {working && (
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                Then you review and save it
              </Typography>
            )}
            <UiOnlyFlag feature="designBuild" />
            {working && onCancel && (
              <Button
                size="small"
                color="inherit"
                onClick={() => setConfirm(true)}
                sx={{ color: "text.secondary", flexShrink: 0 }}
              >
                Stop build
              </Button>
            )}
            {ended && onBack && (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                onClick={onBack}
                sx={{ flexShrink: 0 }}
              >
                Back to the design
              </Button>
            )}
            <Button
              size="small"
              color="inherit"
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
              endIcon={
                <Iconify
                  icon="solar:alt-arrow-down-linear"
                  width={14}
                  sx={{
                    transform: open ? "rotate(180deg)" : "none",
                    transition: (t) => t.transitions.create("transform"),
                    "@media (prefers-reduced-motion: reduce)": {
                      transition: "none",
                    },
                  }}
                />
              }
              sx={{ color: "text.secondary", flexShrink: 0 }}
            >
              {open ? "Hide steps" : "Show steps"}
            </Button>
          </Stack>
          <Collapse in={open} timeout={timeout}>
            <Box sx={{ pb: 1.5 }}>
              <DesignBuildView
                build={shown}
                onLeaveOut={() => {}}
                timeout={timeout}
                outcome={false}
              />
            </Box>
          </Collapse>
        </Box>
      </Box>
      <ConfirmDialog
        open={confirm && working}
        onClose={() => setConfirm(false)}
        title="Stop the build?"
        content="It stops where it is. Nothing is saved and your draft is kept."
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setConfirm(false);
              onCancel();
            }}
          >
            Stop build
          </Button>
        }
      />
    </Collapse>
  );
}
DesignBuildStrip.propTypes = {
  // useDesignBuild's build of the design this environment comes from.
  build: PropTypes.object,
  // The open tab, to stop following once someone picks another.
  tab: PropTypes.string,
  // (tab) moves the page to the tab the build is writing.
  onFollow: PropTypes.func,
  // Stops a build still running or being checked.
  onCancel: PropTypes.func,
  // Opens the design again after the build failed or was cancelled.
  onBack: PropTypes.func,
};
