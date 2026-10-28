import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import {
  SAMPLE_CURRENT_ENV_VERSION,
  SAMPLE_NEXT_ENV_VERSION,
  SAMPLE_PENDING_EDITS,
} from "src/api/simulate-environments/_fixtures/detail/versions";
import SoonChip from "../components/SoonChip";
import UiOnlyFlag from "../components/UiOnlyFlag";
import useUiSample from "../hooks/useUiSample";
import { UI_ONLY_SAVED } from "./gaps/NeedsInputButton";
import useEnvViewAs from "./versions/useEnvViewAs";
import VersionHistoryMenu from "./versions/VersionHistoryMenu";
import RollbackDialog from "./versions/RollbackDialog";

// What is being tested, against what: Vel's "Agent v2 × environment v7 · N
// scenarios", with History. The agent and its version are real. Our API has
// no environment versions, so that part is a soon chip; ?uiSample=versions
// shows Vel's version, unsaved edits, history, rollback and view-as.
export default function VersionBar({ env, envState }) {
  const sample = useUiSample("versions");
  const { viewAs, setViewAs, clearViewAs } = useEnvViewAs();
  const viewing = sample && viewAs;
  const [historyAnchor, setHistoryAnchor] = useState(null);
  const [rollbackTo, setRollbackTo] = useState(null);
  const subject = env?.testSubject;
  const scenarioCount =
    env?.counts?.scenarios ?? envState?.scenarios?.length ?? 0;
  const envLabel = viewing ? viewAs : SAMPLE_CURRENT_ENV_VERSION;
  const strong = (t) => (
    <Box component="span" sx={{ color: "text.primary", fontWeight: 600 }}>
      {t}
    </Box>
  );

  return (
    <>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 0.875,
          borderBottom: "1px solid",
          borderColor: "divider",
          flexShrink: 0,
          flexWrap: "wrap",
          rowGap: 1,
        }}
      >
        <CustomTooltip
          show
          arrow
          size="small"
          title={
            <>
              {subject && (
                <Box
                  component="span"
                  sx={{ display: "block", fontWeight: 600, mb: 0.5 }}
                >
                  {`${subject.name || "Agent"} ${subject.active_version || ""}, the test subject`}
                </Box>
              )}
              An agent change moves only the agent. The environment version
              moves when the world, scenarios, sub-goals, evaluations or
              simulated caller change.
            </>
          }
        >
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ minWidth: 0, cursor: "default" }}
          >
            <Iconify
              icon="solar:box-linear"
              width={14}
              sx={{ color: "text.subtitle", mr: 0.25 }}
            />
            <Typography
              noWrap
              sx={{
                typography: "s2",
                color: "text.secondary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {subject ? (
                <>
                  Agent{" "}
                  {strong(
                    subject.active_version || subject.name || "connected",
                  )}
                </>
              ) : (
                "No agent yet"
              )}
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.disabled", mx: 0.5 }}
              >
                ×
              </Box>
              environment{sample ? <> {strong(envLabel)}</> : null}
            </Typography>
            {!sample && (
              <SoonChip
                label="Version" detail="Every change to the world, scenarios, evaluations or simulated caller would save a new environment version, so runs can be compared like for like."
                what="The environment version"
                size="tiny"
              />
            )}
            <Typography
              noWrap
              sx={{
                typography: "s3",
                color: "text.subtitle",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {` · ${scenarioCount} scenario${scenarioCount === 1 ? "" : "s"}`}
            </Typography>
          </Stack>
        </CustomTooltip>
        <Box flex={1} />
        {sample && !viewing && (
          <CustomTooltip
            show
            arrow
            size="small"
            title={`${SAMPLE_PENDING_EDITS.detail}. ${SAMPLE_PENDING_EDITS.cost}. Runs use environment ${SAMPLE_CURRENT_ENV_VERSION} until these are saved.`}
          >
            <Stack direction="row" alignItems="center" spacing={0.75}>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.subtitle",
                  cursor: "default",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {`${SAMPLE_PENDING_EDITS.count} edits since environment ${SAMPLE_CURRENT_ENV_VERSION}`}
              </Typography>
              <UiOnlyFlag feature="environmentVersions" />
              <Button
                size="small"
                variant="outlined"
                onClick={() =>
                  enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" })
                }
                sx={{
                  minWidth: 0,
                  flexShrink: 0,
                  whiteSpace: "nowrap",
                  color: "text.primary",
                  borderColor: "divider",
                }}
              >
                {`Save as environment ${SAMPLE_NEXT_ENV_VERSION}`}
              </Button>
            </Stack>
          </CustomTooltip>
        )}
        <Button
          size="small"
          variant="outlined"
          onClick={(e) => setHistoryAnchor(e.currentTarget)}
          startIcon={<Iconify icon="solar:history-linear" width={16} />}
          sx={{
            minWidth: 0,
            flexShrink: 0,
            color: "text.primary",
            borderColor: "divider",
          }}
        >
          History
        </Button>
        <VersionHistoryMenu
          anchor={historyAnchor}
          onClose={() => setHistoryAnchor(null)}
          agent={subject}
          sample={sample}
          onRollback={(label) => {
            setHistoryAnchor(null);
            setRollbackTo(label);
          }}
          onView={(label) => {
            setHistoryAnchor(null);
            setViewAs(label);
          }}
        />
      </Stack>

      {viewing && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2,
            py: 0.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
            flexShrink: 0,
          }}
        >
          <Iconify
            icon="solar:eye-linear"
            width={14}
            sx={{ color: "text.subtitle" }}
          />
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            Viewing as {strong(`environment ${viewAs}`)}. Read-only.
          </Typography>
          <Button size="small" onClick={clearViewAs} sx={{ minWidth: 0 }}>
            Back to current
          </Button>
        </Stack>
      )}

      <RollbackDialog
        target={rollbackTo}
        onClose={() => setRollbackTo(null)}
        onConfirm={() => {
          setRollbackTo(null);
          enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
        }}
      />
    </>
  );
}

VersionBar.propTypes = {
  env: PropTypes.shape({
    id: PropTypes.string,
    testSubject: PropTypes.shape({
      name: PropTypes.string,
      active_version: PropTypes.string,
      versions_count: PropTypes.number,
    }),
    counts: PropTypes.shape({ scenarios: PropTypes.number }),
  }),
  envState: PropTypes.shape({
    scenarios: PropTypes.arrayOf(PropTypes.any),
  }),
};
