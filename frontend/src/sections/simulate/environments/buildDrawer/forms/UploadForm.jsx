import PropTypes from "prop-types";
import { useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, LinearProgress, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import usePanelBuild from "../../hooks/usePanelBuild";
import useCodeUpload from "../../hooks/useCodeUpload";
import FolderInput from "../../components/FolderInput";
import { CODE_UPLOAD_COPY } from "../../codeUpload.constants";
import { OUTLINED_BTN_SX, ROW_BOX_SX, T12 } from "../drawerStyles";
import DrawerEnvValues from "./DrawerEnvValues";
import { Field, FormCard, HintLine, MONO, ToggleRow } from "./formBits";

/** A local folder, uploaded once and built in our sandbox for every run. */
export default function UploadForm({ onBusyChange }) {
  const build = usePanelBuild();
  const { form, set, onDrop, chosen, canGo, summaryLine, buildSource } =
    useCodeUpload(build);
  const {
    folderName,
    entry,
    envText,
    egress,
    secretFiles,
    scenarioCount,
    observabilityEnabled,
    summary,
    uploading,
    progress,
    error,
  } = form;
  const [over, setOver] = useState(false);

  return (
    <FormCard
      intro="A folder, read once and built in our sandbox for every run. Fixes come back as a patch to apply."
      build={build}
      scenarioCount={scenarioCount}
      onScenarioCount={set("scenarioCount")}
      canRun={canGo}
      blockedReason="Add the folder, then preflight checks it and its credentials."
      onRun={() => build.runPreflight(buildSource())}
      hint={
        !chosen
          ? "Add the folder"
          : uploading
            ? "Uploading the folder"
            : !canGo
              ? "Add the folder"
              : null
      }
      onBusyChange={onBusyChange}
    >
      {!chosen ? (
        <Box
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            if (e.dataTransfer?.files) onDrop(e.dataTransfer.files);
          }}
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            px: 1.75,
            py: 1.5,
            border: "1px dashed",
            borderColor: over ? "text.primary" : "divider",
            borderRadius: "6px",
            bgcolor: (t) =>
              over ? alpha(t.palette.text.primary, 0.05) : "transparent",
            transition: "background-color .12s ease, border-color .12s ease",
          }}
        >
          <Iconify
            icon="solar:cloud-upload-linear"
            width={20}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ ...T12, fontWeight: 600 }}>
              {over ? "Drop to upload" : "Drop a folder here"}
            </Typography>
            <Typography sx={{ ...T12, color: "text.secondary" }}>
              .env files and generated dependency folders are left behind.
            </Typography>
          </Box>
          <Button
            component="label"
            variant="outlined"
            startIcon={
              <Iconify icon="solar:upload-minimalistic-linear" width={14} />
            }
            sx={OUTLINED_BTN_SX}
          >
            {CODE_UPLOAD_COPY.choose}
            <FolderInput onPick={onDrop} />
          </Button>
        </Box>
      ) : (
        <Box sx={{ ...ROW_BOX_SX, px: 1.5, py: 1 }}>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Iconify
              icon="solar:folder-with-files-linear"
              width={16}
              sx={{ color: "text.secondary", flexShrink: 0 }}
            />
            <Typography
              noWrap
              sx={{ ...T12, fontFamily: MONO, flex: 1, minWidth: 0 }}
            >
              {summary?.name || folderName}
            </Typography>
            <Typography
              noWrap
              sx={{
                ...T12,
                color: "text.secondary",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {summaryLine()}
            </Typography>
            <Button
              component="label"
              disabled={uploading}
              sx={{
                ...OUTLINED_BTN_SX,
                border: "none",
                bgcolor: "transparent",
              }}
            >
              {CODE_UPLOAD_COPY.replace}
              <FolderInput onPick={onDrop} />
            </Button>
          </Stack>
          {uploading && (
            <LinearProgress
              variant={progress > 0 ? "determinate" : "indeterminate"}
              value={progress}
              sx={{ mt: 1, borderRadius: 1 }}
            />
          )}
        </Box>
      )}
      {error && (
        <Typography sx={{ ...T12, color: "error.main" }}>{error}</Typography>
      )}
      {chosen && (
        <Field
          label="Entry file"
          mono
          value={entry}
          onChange={set("entry")}
          placeholder="app/chat.py"
          helper={
            <HintLine
              text="The file the agent starts in."
              hint="The path from the folder's root, the file that defines the agent's step function."
            />
          }
        />
      )}
      <DrawerEnvValues
        envText={envText}
        onEnvText={set("envText")}
        egress={egress}
        onEgress={set("egress")}
        secretFiles={secretFiles}
        onSecretFiles={set("secretFiles")}
      />
      <ToggleRow
        title="Enable observability"
        body={
          observabilityEnabled
            ? "Its traces come into Observe once the code sends them."
            : "Off. Its traces stay out of Observe."
        }
        hint="We can observe the agent only if its code already sends telemetry (OpenTelemetry or traceAI). Without it the environment still builds."
        checked={observabilityEnabled}
        onChange={set("observabilityEnabled")}
      />
    </FormCard>
  );
}
UploadForm.propTypes = { onBusyChange: PropTypes.func };
