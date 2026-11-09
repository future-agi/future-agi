import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { useOmegaPart } from "src/sections/omega/shell/useOmegaPart";

import CopyField from "../components/CopyField";
import DeployIcon from "../components/DeployIcon";
import {
  BundleError,
  bundleFolder,
  formatBytes,
  readBundleArchive,
} from "../lib/bundle";
import { shortDigest } from "../lib/format";
import SampleProject from "./SampleProject";
import HubProvenance from "./HubProvenance";

// "Use the CLI" shows only once a customer can install a CLI that has
// `omega deploy` (OMEGA_PART DEPLOY_CLI); until then its commands would answer
// "Unknown command: deploy". The other sources name no CLI command either: the
// Bundle file hint states what the page and EE check (lib/bundle.js; EE
// pipeline/bundle.ts), not which tool wrote the archive.
const OPTIONS = [
  { value: "folder", label: "Project folder", icon: "folder" },
  { value: "archive", label: "Bundle file", icon: "archive" },
  {
    value: "cli",
    label: "Use the CLI",
    icon: "terminal",
    part: OMEGA_PART.DEPLOY_CLI,
  },
];

const controlPlane =
  typeof import.meta.env?.VITE_OMEGA_DEPLOY_CONTROL_PLANE === "string"
    ? import.meta.env.VITE_OMEGA_DEPLOY_CONTROL_PLANE
    : "";

function BundleSummary({ bundle }) {
  const { manifest } = bundle;
  return (
    <Box
      data-testid="bundle-summary"
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        p: 1.5,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}>
        <DeployIcon name="check" size={16} sx={{ color: "success.main" }} />
        <Typography typography="s1" fontWeight="fontWeightMedium" noWrap>
          {manifest.name || "Omega project"}
        </Typography>
      </Box>
      <Typography typography="s2" color="text.secondary">
        {manifest.entries.length}{" "}
        {manifest.entries.length === 1 ? "entry" : "entries"}:{" "}
        {manifest.entries.map((e) => e.label || e.id).join(", ")}
      </Typography>
      <Typography
        typography="s2"
        color="text.secondary"
        data-testid="bundle-facts"
      >
        {bundle.files.length} {bundle.files.length === 1 ? "file" : "files"} ·{" "}
        {formatBytes(bundle.size)} compressed · sha256{" "}
        <Box component="span" sx={{ fontFamily: "monospace" }}>
          {shortDigest(bundle.sha256)}
        </Box>
      </Typography>
      <HubProvenance provenance={bundle.provenance} />
      {bundle.excluded.length > 0 && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="bundle-excluded"
        >
          Left out of the bundle:{" "}
          {bundle.excluded
            .slice(0, 6)
            .map((item) => `${item.path} (${item.reason})`)
            .join(", ")}
          {bundle.excluded.length > 6
            ? ` and ${bundle.excluded.length - 6} more`
            : ""}
        </Typography>
      )}
    </Box>
  );
}

BundleSummary.propTypes = { bundle: PropTypes.object.isRequired };

function Picker({ kind, onPicked, busy }) {
  const input = useRef(null);
  const folder = kind === "folder";
  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: { xs: "column", sm: "row" },
        alignItems: { xs: "flex-start", sm: "center" },
        gap: 1.5,
        p: 2,
        border: "1px dashed",
        borderColor: "divider",
        borderRadius: 1,
      }}
    >
      <input
        ref={input}
        type="file"
        hidden
        data-testid={folder ? "folder-input" : "archive-input"}
        {...(folder
          ? { webkitdirectory: "", directory: "", multiple: true }
          : { accept: ".gz,.tgz,application/gzip,application/x-gzip" })}
        onChange={(event) => {
          const files = event.target.files;
          if (files && files.length > 0) onPicked(folder ? files : files[0]);
          event.target.value = "";
        }}
      />
      <Button
        variant="outlined"
        color="inherit"
        size="small"
        disabled={busy}
        onClick={() => input.current?.click()}
        startIcon={
          <DeployIcon name={folder ? "folder" : "archive"} size={16} />
        }
      >
        {busy ? "Reading…" : folder ? "Choose folder" : "Choose .tar.gz"}
      </Button>
      <Typography typography="s2" color="text.secondary">
        {folder
          ? "The folder that holds omega.studio.json. It is bundled here, in your browser."
          : "A .tar.gz with omega.studio.json at its top level, holding only files and folders. It is sent as it is."}
      </Typography>
    </Box>
  );
}

Picker.propTypes = {
  kind: PropTypes.oneOf(["folder", "archive"]).isRequired,
  onPicked: PropTypes.func.isRequired,
  busy: PropTypes.bool,
};

function CliSource({ onUseBundle }) {
  return (
    <Box
      data-testid="cli-source"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Typography typography="s1" color="text.secondary">
        Bundle the project with the Omega CLI, then upload the file here. The
        bundle leaves out local state, dependencies and files that may hold
        secrets.
      </Typography>
      <CopyField
        value="omega deploy bundle --project . --out omega-project.tar.gz"
        label="bundle command"
        testId="cli-bundle-command"
      />
      <Box>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={onUseBundle}
          startIcon={<DeployIcon name="archive" size={16} />}
        >
          Upload the bundle
        </Button>
      </Box>
      {controlPlane && (
        <>
          <Typography typography="s1" color="text.secondary" sx={{ mt: 1 }}>
            Or deploy straight from the CLI with a control-plane token:
          </Typography>
          <CopyField
            value={`omega deploy --project . --control-plane ${controlPlane} --token-file <token-file>`}
            label="deploy command"
            testId="cli-deploy-command"
          />
        </>
      )}
    </Box>
  );
}

CliSource.propTypes = { onUseBundle: PropTypes.func.isRequired };

/** Step 1: where the project comes from. Calls onReady(bundle) once it checks out. */
export default function SourceStep({ bundle, onBundle }) {
  const cliReleased = useOmegaPart(OMEGA_PART.DEPLOY_CLI);
  const options = [
    ...(bundle?.source === "hub" ? [{ value: "hub", label: "Hub source", icon: "archive" }] : []),
    ...OPTIONS.filter((option) => !option.part || cliReleased),
  ];
  const [chosen, setMode] = useState(bundle?.source || "folder");
  const mode = options.some((option) => option.value === chosen)
    ? chosen
    : "folder";
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);

  const read = async (picked) => {
    setBusy(true);
    setProblem(null);
    try {
      const next =
        mode === "folder"
          ? await bundleFolder(picked)
          : await readBundleArchive(picked);
      onBundle(next);
    } catch (error) {
      onBundle(null);
      setProblem(
        error instanceof BundleError
          ? error.message
          : "The project could not be read.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <ToggleButtonGroup
        exclusive
        size="small"
        value={mode}
        onChange={(_, value) => {
          if (!value) return;
          setMode(value);
          setProblem(null);
        }}
        aria-label="Project source"
        sx={{
          flexWrap: "wrap",
          "& .MuiToggleButton-root": {
            gap: 0.75,
            px: 1.5,
            textTransform: "none",
          },
        }}
      >
        {options.map((option) => (
          <ToggleButton
            key={option.value}
            value={option.value}
            data-testid={`source-${option.value}`}
          >
            <DeployIcon name={option.icon} size={16} />
            {option.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>
      {mode === "hub" ? null : mode === "cli" ? (
        <CliSource onUseBundle={() => setMode("archive")} />
      ) : (
        <Picker kind={mode} onPicked={read} busy={busy} />
      )}
      {mode !== "cli" && (
        <SampleProject
          disabled={busy}
          onUse={(sample) => {
            setMode("archive");
            setProblem(null);
            onBundle(sample);
          }}
        />
      )}
      {problem && (
        <Alert severity="error" variant="outlined" data-testid="source-problem">
          {problem}
        </Alert>
      )}
      {bundle && <BundleSummary bundle={bundle} />}
    </Box>
  );
}

SourceStep.propTypes = {
  bundle: PropTypes.object,
  onBundle: PropTypes.func.isRequired,
};
