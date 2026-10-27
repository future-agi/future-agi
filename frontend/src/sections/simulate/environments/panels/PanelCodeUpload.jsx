import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, LinearProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import Field from "../components/Field";
import ContinueRow from "../components/ContinueRow";
import EnvironmentValues from "./EnvironmentValues";
import ScenarioCount from "./ScenarioCount";
import ObservabilityChoice from "./ObservabilityChoice";
import { isValidScenarioCount } from "./scenarioCountRules";
import RuntimePreflight from "./RuntimePreflight";
import { CreditExhaustionBanner } from "src/components/CreditExhaustionBanner";
import ParallelismField from "./ParallelismField";
import usePanelBuild from "../hooks/usePanelBuild";
import useCodeUpload from "../hooks/useCodeUpload";
import FolderInput from "../components/FolderInput";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";

export default function PanelCodeUpload() {
  const build = usePanelBuild();
  const { form, set, onDrop, chosen, canGo, summaryLine, buildSource } = useCodeUpload(build);
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

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      {!chosen ? (
        <Box
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); if (e.dataTransfer?.files) onDrop(e.dataTransfer.files); }}
          sx={{
            border: "1px dashed",
            borderColor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.25) : th.palette.divider,
            borderRadius: 1.5,
            bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
            p: 3,
            textAlign: "center",
          }}
        >
          <Iconify icon="solar:cloud-upload-linear" width={28} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mt: 1 }}>
            {CODE_UPLOAD_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {CODE_UPLOAD_COPY.hint}
          </Typography>
          <Button
            component="label"
            size="small"
            variant="outlined"
            sx={{ mt: 1.5, typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {CODE_UPLOAD_COPY.choose}
            <FolderInput onPick={onDrop} />
          </Button>
        </Box>
      ) : (
        // Uploaded/uploading summary — the archive is all-or-nothing, so we show a
        // single summary + Replace rather than an editable per-file list.
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1.5,
            bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
            p: 1.75,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Iconify icon="solar:folder-with-files-bold" width={24} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }} noWrap>
                {summary?.name || folderName || CODE_UPLOAD_COPY.title}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }} noWrap>
                {summaryLine()}
              </Typography>
            </Box>
            <Button
              component="label"
              size="small"
              color="inherit"
              disabled={uploading}
              sx={{ flexShrink: 0, typography: "s2", fontWeight: "fontWeightBold" }}
            >
              {CODE_UPLOAD_COPY.replace}
              <FolderInput onPick={onDrop} />
            </Button>
          </Stack>
          {uploading && (
            <LinearProgress
              variant={progress > 0 ? "determinate" : "indeterminate"}
              value={progress}
              sx={{ mt: 1.25, borderRadius: 1 }}
            />
          )}
        </Box>
      )}

      {error && (
        <Typography sx={{ typography: "s3" }} color="error.main">
          {error}
        </Typography>
      )}

      {chosen && (
        <Field
          label="Entry file"
          value={entry} onChange={set("entry")}
          mono
          helper={CODE_UPLOAD_COPY.entryHelper}
        />
      )}

      <EnvironmentValues
        envText={envText} onEnvText={set("envText")}
        egress={egress} onEgress={set("egress")}
        secretFiles={secretFiles} onSecretFiles={set("secretFiles")}
      />
      <ScenarioCount value={scenarioCount} onChange={set("scenarioCount")} />
      <ObservabilityChoice checked={observabilityEnabled} onChange={set("observabilityEnabled")} />
      <ParallelismField
        value={build.parallelism}
        input={build.parallelismInput}
        onChange={build.setParallelism}
        enabled={build.parallelismEnabled}
        admitted={build.admittedParallelism}
      />
      <RuntimePreflight
        status={build.status}
        canRun={canGo}
        onRun={() => build.runPreflight(buildSource())}
        result={build.result}
        error={build.error}
      />
      <CreditExhaustionBanner
        error={build.creditExhaustion.error}
        onUpgrade={build.creditExhaustion.onUpgrade}
        onDismiss={build.creditExhaustion.onDismiss}
      />
      <ContinueRow
        disabled={!build.readyToSubmit || !isValidScenarioCount(scenarioCount)}
        busy={build.committing}
        hint={build.status === "done" ? "Resolve the failed preflight checks above to continue" : "Run preflight to check your setup before building"}
        onClick={build.commitBuild}
      />
    </Stack>
  );
}
