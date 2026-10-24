import PropTypes from "prop-types";
import { useEffect, useMemo, useState } from "react";
import { useSnackbar } from "notistack";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  pendingImprovementScenarios,
  addImprovementScenariosPatch,
} from "../../_mock/improvementScenarios";
import SourceBadge from "./SourceBadge";
import { scenarioTitle } from "../../_mock/scenarios";

const CHECKBOX_SX = {
  p: 0.5,
  color: "text.disabled",
  "&.Mui-checked": { color: "text.primary" },
};

/**
 * "4 scenarios from improvements aren't in this environment — Review".
 *
 * Scenarios an improvement wrote (to reproduce what it was fixing) are the
 * improvement's own until someone adds them. A quiet strip says how many are
 * waiting; Review lists them by improvement, and Add to environment brings
 * the picked ones in, with their source badge —
 * the same write the improvement page makes.
 */
export default function ImprovementScenariosStrip({
  envState,
  patch,
  locked = false,
}) {
  const groups = useMemo(
    () => pendingImprovementScenarios(envState),
    [envState],
  );
  const total = groups.reduce((n, g) => n + g.rows.length, 0);
  const [open, setOpen] = useState(false);

  if (!total) return null;

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{
          mb: 1.5,
          px: 2,
          py: 1,
          borderRadius: "8px",
          border: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:magic-stick-3-linear"
          width={16}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            {`${total} scenario${total === 1 ? "" : "s"} from improvements ${total === 1 ? "isn't" : "aren't"} in this environment`}
          </Typography>
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Improvements wrote these to reproduce what they were fixing. They
            stay with the improvement until you add them.
          </Typography>
        </Box>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setOpen(true)}
          sx={{ color: "text.primary", borderColor: "divider", flexShrink: 0 }}
        >
          Review
        </Button>
      </Stack>
      <ImprovementScenariosDialog
        envState={envState}
        patch={patch}
        locked={locked}
        open={open}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
ImprovementScenariosStrip.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
};

/**
 * The review itself — the waiting scenarios by improvement, each picked by
 * default; Add to environment brings the picked ones in. Controlled, so the
 * Scenarios toolbar's "Needs review" list can open it too.
 */
export function ImprovementScenariosDialog({
  envState,
  patch,
  locked = false,
  open,
  onClose,
}) {
  const { enqueueSnackbar } = useSnackbar();
  const groups = useMemo(
    () => pendingImprovementScenarios(envState),
    [envState],
  );
  const [picked, setPicked] = useState({});
  /* Every waiting scenario starts picked each time the review opens. */
  useEffect(() => {
    if (open)
      setPicked(
        Object.fromEntries(
          groups.flatMap((g) => g.rows.map((r) => [r.id, true])),
        ),
      );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const count = Object.values(picked).filter(Boolean).length;
  const add = () => {
    /* One improvement at a time, each patch read off the state the last one left. */
    let es = envState;
    let merged = {};
    groups.forEach(({ record, rows }) => {
      const ids = rows.filter((r) => picked[r.id]).map((r) => r.id);
      if (!ids.length) return;
      const p = addImprovementScenariosPatch(es, record, ids);
      es = { ...es, ...p };
      merged = { ...merged, ...p };
    });
    if (merged.scenarios) patch(merged);
    onClose();
    enqueueSnackbar(
      `Added ${count} scenario${count === 1 ? "" : "s"} — ${count === 1 ? "it runs" : "they run"} from the next run`,
      { variant: "success" },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { maxWidth: 720 } }}
      fullWidth
    >
      <DialogTitle sx={{ typography: "m2", fontWeight: 600 }}>
        Scenarios from improvements
      </DialogTitle>
      <DialogContent>
        <Typography sx={{ typography: "s2", color: "text.secondary", mb: 1.5 }}>
          Added ones join the environment with their source badge, and run from
          the next run.
        </Typography>
        <Stack spacing={1.5}>
          {groups.map(({ record, rows }) => (
            <Box
              key={record.id}
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
                overflow: "hidden",
              }}
            >
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ px: 1.5, py: 0.875, bgcolor: "background.neutral" }}
              >
                <Typography
                  sx={{
                    typography: "s2",
                    fontWeight: 600,
                    flex: 1,
                    minWidth: 0,
                  }}
                  noWrap
                >
                  {record.name || record.trigger?.issueTitle || record.id}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {`${rows.length} scenario${rows.length === 1 ? "" : "s"}`}
                </Typography>
              </Stack>
              {rows.map((r) => (
                <Stack
                  key={r.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{
                    px: 1,
                    py: 0.625,
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Checkbox
                    size="small"
                    checked={!!picked[r.id]}
                    disabled={locked}
                    onChange={() =>
                      setPicked((p) => ({ ...p, [r.id]: !p[r.id] }))
                    }
                    sx={CHECKBOX_SX}
                  />
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography
                      noWrap
                      sx={{ typography: "s2", fontWeight: 500 }}
                    >
                      {scenarioTitle(r.title) || r.name}
                    </Typography>
                    <Typography
                      noWrap
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      {r.summary || r.situation}
                    </Typography>
                  </Box>
                  <SourceBadge
                    scenario={{
                      ...r,
                      fromImprovement: r.fromImprovement || {
                        id: record.id,
                        name: record.name,
                      },
                    }}
                  />
                </Stack>
              ))}
            </Box>
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={locked || count === 0}
          onClick={add}
        >
          {count ? `Add ${count} to environment` : "Add to environment"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
ImprovementScenariosDialog.propTypes = {
  envState: PropTypes.object,
  patch: PropTypes.func,
  locked: PropTypes.bool,
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
