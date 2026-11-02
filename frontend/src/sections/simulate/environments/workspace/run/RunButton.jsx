import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { alpha } from "@mui/material/styles";
import {
  Box,
  Button,
  ButtonBase,
  Menu,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { useMyEnvironments } from "src/api/simulate-environments/environments";
import { EMPTY_ENV_FILTERS } from "src/api/simulate-environments/environmentListParams";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import SoonChip from "../../components/SoonChip";
import useUiSample from "../../hooks/useUiSample";
import { ENV_STATUS } from "../../myEnvironments.constants";
import RunFitDialog from "./RunFitDialog";

// An environment that can't take a run right now: still building or going away.
const NOT_READY = new Set([
  ENV_STATUS.BUILDING,
  ENV_STATUS.FINALIZING,
  ENV_STATUS.CANCELLING,
  ENV_STATUS.CANCELLED,
]);

export const RUN_ELSEWHERE_UI_ONLY =
  "Ran here. Running in other environments is UI only.";

// Vel's Run: one button with its menu as the end of the same shape. The menu
// offers the agent's other environments as a run group; starting runs there
// has no API yet, so picks are UI only and Run starts this one alone. The
// tooltip says what a run is; the credit estimate is still to come.
export default function RunButton({
  env,
  scenarioCount = 0,
  repeats,
  canRun,
  runBlockedReason,
  onStartRun,
}) {
  const sampleFit = useUiSample("toolfit");
  const [menu, setMenu] = useState(null);
  const [picked, setPicked] = useState([]);
  const [fitOpen, setFitOpen] = useState(false);
  const unit = env?.surface === "voice" ? "call" : "conversation";
  const agentId = env?.testSubject?.id;
  const { data } = useMyEnvironments({
    page: 0,
    pageSize: 50,
    filters: { ...EMPTY_ENV_FILTERS, agentId: agentId || "" },
  });
  const candidates = agentId
    ? (data?.rows ?? []).filter((row) => row.id !== env?.id)
    : [];
  const groupWith = picked.filter((id) => candidates.some((c) => c.id === id));
  const label = groupWith.length
    ? `Run in ${groupWith.length + 1}`
    : "Run simulation";
  const total = scenarioCount * repeats;

  const start = () => {
    onStartRun?.(undefined, repeats);
    if (groupWith.length)
      enqueueSnackbar(RUN_ELSEWHERE_UI_ONLY, { variant: "info" });
  };
  const onRun = () => (sampleFit ? setFitOpen(true) : start());
  const toggle = (id) =>
    setPicked((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );

  const tip = canRun ? (
    <Stack spacing={0.5} sx={{ py: 0.25 }}>
      <Typography sx={{ typography: "s3", color: "inherit" }}>
        {`${scenarioCount} ${scenarioCount === 1 ? "scenario" : "scenarios"} × ${repeats} ${repeats === 1 ? "repeat" : "repeats"} = ${total} ${unit}${total === 1 ? "" : "s"}`}
      </Typography>
      <Box>
        <SoonChip
          label="Estimate" detail="The credits a run will use, shown before you start it."
          what="A credit estimate before a run"
          size="tiny"
        />
      </Box>
    </Stack>
  ) : (
    runBlockedReason || ""
  );

  return (
    <>
      <Box
        sx={{ display: "inline-flex", alignItems: "stretch", flexShrink: 0 }}
      >
        <CustomTooltip show={!!tip} title={tip} size="small" arrow>
          <span style={{ display: "inline-flex" }}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              disabled={!canRun}
              onClick={onRun}
              startIcon={<Iconify icon="solar:play-bold" width={16} />}
              sx={{
                whiteSpace: "nowrap",
                borderTopRightRadius: 0,
                borderBottomRightRadius: 0,
              }}
            >
              {label}
            </Button>
          </span>
        </CustomTooltip>
        <CustomTooltip
          show
          size="small"
          arrow
          title={
            groupWith.length
              ? `Running in ${groupWith.length + 1} environments. Change.`
              : "More ways to run"
          }
        >
          <span style={{ display: "inline-flex" }}>
            <Button
              variant="contained"
              color="primary"
              size="small"
              aria-label="More ways to run"
              disabled={!canRun}
              onClick={(e) => setMenu(e.currentTarget)}
              sx={{
                minWidth: 0,
                px: 0.75,
                borderTopLeftRadius: 0,
                borderBottomLeftRadius: 0,
                borderLeft: (t) =>
                  `1px solid ${alpha(t.palette.primary.contrastText, 0.16)}`,
              }}
            >
              <Iconify icon="solar:alt-arrow-down-linear" width={14} />
            </Button>
          </span>
        </CustomTooltip>
      </Box>

      <Menu
        anchorEl={menu}
        open={!!menu}
        onClose={() => setMenu(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 360, mt: 0.5 } } }}
      >
        <Box
          sx={{ px: 1, py: 1.25 }}
          onKeyDown={(e) => e.key !== "Escape" && e.stopPropagation()}
        >
          <Stack direction="row" alignItems="flex-start" spacing={1.25}>
            <Iconify
              icon="solar:layers-linear"
              width={16}
              sx={{ mt: "2px", color: "text.secondary", flexShrink: 0 }}
            />
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Stack direction="row" alignItems="center" spacing={0.75}>
                <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                  Run in more environments
                </Typography>
                <UiOnlyFlag feature="runInMoreEnvironments" />
              </Stack>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  whiteSpace: "normal",
                  mb: 1,
                }}
              >
                A change to the agent can fix one environment and break another.
              </Typography>
              {candidates.length ? (
                <Stack direction="row" flexWrap="wrap" sx={{ gap: 0.75 }}>
                  {candidates.map((c) => (
                    <EnvChip
                      key={c.id}
                      name={c.name}
                      ready={!NOT_READY.has(c.status)}
                      on={groupWith.includes(c.id)}
                      onToggle={() => toggle(c.id)}
                    />
                  ))}
                </Stack>
              ) : (
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  No other environment tests this agent yet.
                </Typography>
              )}
            </Box>
          </Stack>
        </Box>
      </Menu>

      <RunFitDialog
        open={fitOpen}
        onClose={() => setFitOpen(false)}
        onRunAnyway={() => {
          setFitOpen(false);
          start();
        }}
      />
    </>
  );
}

RunButton.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    surface: PropTypes.string,
    testSubject: PropTypes.shape({ id: PropTypes.string }),
  }).isRequired,
  scenarioCount: PropTypes.number,
  repeats: PropTypes.number.isRequired,
  canRun: PropTypes.bool,
  runBlockedReason: PropTypes.string,
  onStartRun: PropTypes.func,
};

// One environment as a checkable chip; one still building can't join.
function EnvChip({ name, ready, on, onToggle }) {
  return (
    <ButtonBase
      disabled={!ready}
      onClick={onToggle}
      aria-pressed={on}
      aria-label={name}
      sx={{
        px: 1,
        height: 26,
        borderRadius: "4px",
        border: "1px solid",
        borderColor: on ? "primary.main" : "divider",
        bgcolor: on ? "action.selected" : "transparent",
        color: ready ? "text.primary" : "text.disabled",
        typography: "s3",
        fontWeight: 600,
        gap: 0.5,
      }}
    >
      <Iconify
        icon={on ? "solar:check-square-bold" : "solar:square-linear"}
        width={14}
      />
      {name}
    </ButtonBase>
  );
}
EnvChip.propTypes = {
  name: PropTypes.string,
  ready: PropTypes.bool,
  on: PropTypes.bool,
  onToggle: PropTypes.func,
};
