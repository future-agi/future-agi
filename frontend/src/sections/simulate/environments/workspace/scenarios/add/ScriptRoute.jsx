import PropTypes from "prop-types";
import { useEffect, useMemo, useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Stack,
  Typography,
  Button,
  Checkbox,
  IconButton,
  LinearProgress,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import { Upload } from "src/components/upload";
import { formatFileSize } from "src/utils/utils";
import { sampleScriptScenarios } from "src/api/simulate-environments/_fixtures/detail/scenarioSources";
import SectionCard from "../../../components/SectionCard";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../../gaps/NeedsInputButton";
import { personaSubline } from "../personaLabels";
import { ENV_SHAPE, ENV_STATE_SHAPE } from "../scenarios.shapes";

const READ_MS = 1100;

// Vel's script upload: turn a call script, SOP or runbook into the scenarios
// it describes, shown for review rather than committed the moment the file
// lands. No backend yet: the file is accepted but never uploaded, the beats
// are sample data, and adding saves nothing.
export default function ScriptRoute({ env, envState, onClose }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [extracted, setExtracted] = useState(null);
  const [checked, setChecked] = useState({});
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const alreadyIn = useMemo(
    () => new Set((envState?.scenarios || []).map((s) => s.id)),
    [envState],
  );

  const onDrop = (accepted) => {
    const next = accepted?.[0];
    if (!next) return;
    setFile(next);
    setExtracted(null);
    setChecked({});
    setBusy(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      setExtracted(sampleScriptScenarios(env, next.name));
      setBusy(false);
    }, READ_MS);
  };

  const clear = () => {
    clearTimeout(timer.current);
    setFile(null);
    setBusy(false);
    setExtracted(null);
    setChecked({});
  };

  const chosen = useMemo(
    () =>
      (extracted || []).filter((r) => checked[r.id] && !alreadyIn.has(r.id)),
    [extracted, checked, alreadyIn],
  );

  const commit = () => {
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    onClose();
  };

  return (
    <SectionCard
      title={
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            component="span"
            sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }}
          >
            Upload a script
          </Typography>
          <UiOnlyFlag feature="scenarioSources" />
        </Stack>
      }
      subtitle="Turn a call script, SOP or runbook into the scenarios it describes"
      action={
        <Button
          variant="contained"
          color="primary"
          size="small"
          disabled={chosen.length === 0}
          onClick={commit}
          sx={{ whiteSpace: "nowrap", flexShrink: 0 }}
        >
          {chosen.length
            ? `Add ${chosen.length} scenario${chosen.length === 1 ? "" : "s"}`
            : "Add scenarios"}
        </Button>
      }
    >
      <Box sx={{ p: 2.5 }}>
        {file ? (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{
              p: 2,
              borderRadius: "8px",
              border: "1px solid",
              borderColor: "divider",
            }}
          >
            <SvgColor
              src="/assets/icons/components/ic_script.svg"
              sx={{
                width: 24,
                height: 24,
                color: "primary.main",
                flexShrink: 0,
              }}
            />
            <Box flex={1} minWidth={0}>
              <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                {file.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                {formatFileSize(file.size)}
              </Typography>
            </Box>
            <IconButton
              size="small"
              onClick={clear}
              aria-label="Remove the script"
            >
              <Iconify
                icon="solar:close-circle-linear"
                width={16}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </Stack>
        ) : (
          <Upload
            showDropRejection={false}
            hidePreview
            showIllustration={false}
            heading="Drop a script here, or browse"
            description=".txt or .pdf"
            uploadIcon={
              <SvgColor
                src="/assets/icons/components/ic_script.svg"
                sx={{ width: 32, height: 32, color: "primary.main" }}
              />
            }
            actionButton={
              <Button size="small" variant="outlined" color="primary">
                Browse files
              </Button>
            }
            accept={{ "text/plain": [".txt"], "application/pdf": [".pdf"] }}
            sx={{ py: 3 }}
            onDrop={onDrop}
          />
        )}

        {(busy || extracted) && (
          <Box
            sx={{
              mt: 2,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              overflow: "hidden",
            }}
          >
            {busy ? (
              <Stack
                direction="row"
                alignItems="center"
                spacing={1.25}
                sx={{ px: 2, py: 2 }}
              >
                <LinearProgress
                  sx={{ width: 90, height: 4, borderRadius: 2 }}
                />
                <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                  {`Reading ${file?.name}…`}
                </Typography>
              </Stack>
            ) : (
              <>
                <Stack
                  direction="row"
                  alignItems="center"
                  spacing={2}
                  sx={{
                    px: 2,
                    py: 1.25,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                  }}
                >
                  <Box flex={1}>
                    <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                      {`${extracted.length} scenarios found in ${file?.name}`}
                    </Typography>
                    <Typography
                      sx={{ typography: "s3", color: "text.subtitle" }}
                    >
                      One per beat in the script · click to include
                    </Typography>
                  </Box>
                  <Button
                    size="small"
                    onClick={() =>
                      setChecked(
                        Object.fromEntries(extracted.map((r) => [r.id, true])),
                      )
                    }
                    sx={{ minWidth: 0, color: "primary.main" }}
                  >
                    Select all
                  </Button>
                </Stack>
                <Stack
                  divider={
                    <Box
                      sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                    />
                  }
                >
                  {extracted.map((r) => (
                    <ExtractedRow
                      key={r.id}
                      row={r}
                      checked={!!checked[r.id] || alreadyIn.has(r.id)}
                      onToggle={() =>
                        !alreadyIn.has(r.id) &&
                        setChecked((c) => ({ ...c, [r.id]: !c[r.id] }))
                      }
                    />
                  ))}
                </Stack>
              </>
            )}
          </Box>
        )}
      </Box>
    </SectionCard>
  );
}
ScriptRoute.propTypes = {
  env: ENV_SHAPE,
  envState: ENV_STATE_SHAPE,
  onClose: PropTypes.func.isRequired,
};

// One beat read out of the script: tick to include. The whole row is the
// checkbox, as in Vel's compact scenario row.
function ExtractedRow({ row, checked, onToggle }) {
  const subline = personaSubline(row.persona);
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={2}
      role="checkbox"
      aria-checked={checked}
      aria-label={row.title}
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onToggle();
        }
      }}
      sx={{
        px: 2.5,
        py: 1,
        cursor: "pointer",
        "&:hover": { bgcolor: "action.hover" },
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
          outlineOffset: -2,
        },
      }}
    >
      <Checkbox
        size="small"
        checked={checked}
        tabIndex={-1}
        disableRipple
        inputProps={{ "aria-hidden": true }}
        sx={{ p: 0, flexShrink: 0 }}
      />
      <Box sx={{ flex: 1.4, minWidth: 0 }}>
        <Typography noWrap sx={{ typography: "s2", fontWeight: 500 }}>
          {row.title}
        </Typography>
        <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
          {row.task}
        </Typography>
      </Box>
      <Box
        sx={{
          width: 200,
          flexShrink: 0,
          minWidth: 0,
          display: { xs: "none", md: "block" },
        }}
      >
        <Typography noWrap sx={{ typography: "s2", color: "text.primary" }}>
          {row.persona?.name}
        </Typography>
        {subline && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {subline}
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.5}
        sx={{ width: 62, flexShrink: 0, display: { xs: "none", sm: "flex" } }}
      >
        <Iconify
          icon="solar:chat-round-line-linear"
          width={14}
          sx={{ color: "text.subtitle" }}
        />
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {`~${row.turns}`}
        </Typography>
      </Stack>
    </Stack>
  );
}
ExtractedRow.propTypes = {
  row: PropTypes.object.isRequired,
  checked: PropTypes.bool,
  onToggle: PropTypes.func,
};
