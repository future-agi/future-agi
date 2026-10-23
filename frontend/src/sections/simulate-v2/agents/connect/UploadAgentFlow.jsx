import PropTypes from "prop-types";
import { useMemo, useRef, useState } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Button, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { repoDetectionOf } from "../../_mock/connectAgent";
import { envValuesSourceOf } from "../../_mock/envValues";
import { Field } from "../../environments/StartEnvironment";
import ConnectSteps from "./ConnectSteps";
import EnvironmentValues from "./EnvironmentValues";
import { HintLine } from "./replicationBits";
import { useConnectSubmitLabel } from "./connectSubmit";
import { credentialCheck, egressCheck, factCheck, parseEgress, usePreflight } from "./preflight";

const MONO = "ui-monospace, Menlo, monospace";
const STEPS = ["Connect"];
const sizeOf = (n) => (n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round((n || 0) / 1024))} KB`);

/**
 * Code without a repository: a folder or a .zip, read once and built in our
 * hosted sandbox. Like a repository, the entry file says where the agent
 * starts, and the environment values its code reads are asked for on the
 * same screen. Fixes come back as a patch to apply.
 */
export default function UploadAgentFlow({ title, onSave, initial = null }) {
  const submitLabel = useConnectSubmitLabel();
  const inputRef = useRef(null);
  /* Files dropped on New agent's upload area come along, so they're not dropped twice. */
  const [files, setFiles] = useState(initial?.files || []);
  const [entry, setEntry] = useState(initial?.files?.[0]?.name || "");
  const [name, setName] = useState("");
  const [envValues, setEnvValues] = useState([]);
  const [credFiles, setCredFiles] = useState([]);
  const [egressText, setEgressText] = useState("");
  const [over, setOver] = useState(false);
  const egress = parseEgress(egressText);

  const add = (list) => {
    const added = Array.from(list || []).map((f) => ({ name: f.name, size: f.size }));
    setFiles((prev) => [...prev, ...added.filter((a) => !prev.some((p) => p.name === a.name))]);
    if (!entry && added[0]) setEntry(added[0].name);
  };

  /* What the upload's code reads, from its entry file — names only. */
  const file = entry.trim().split(":")[0];
  const expectedNames = useMemo(() => {
    if (!files.length || !file) return [];
    const folder = file.includes("/") ? file.replace(/\/[^/]*$/, "") : ".";
    const language = /\.(ts|tsx|js|mjs)$/.test(file) ? "TypeScript" : "Python";
    return repoDetectionOf({ repo: { name: files.map((f) => f.name).join("+"), language, agents: [] }, folder, branch: "upload", found: null }).credentialKeys.value;
  }, [files, file]);

  const ready = files.length > 0 && !!entry.trim();
  const preflight = usePreflight([
    factCheck({ id: "files", label: "Upload", ok: ready, okText: `${files.length} file${files.length === 1 ? "" : "s"} · entry ${entry.trim()} — unpacked in the sandbox.`, failText: files.length ? "Name the entry file." : "Add at least one file." }),
    ...expectedNames.filter((n) => /KEY|TOKEN|SECRET|PASSWORD|DSN/i.test(n)).map((n) => credentialCheck({ id: `env:${n}`, label: n, ref: envValues.find((v) => v.name === n)?.ref || envValues.find((v) => v.name === n)?.value || "" })),
    ...(egress.length ? [egressCheck(egress)] : []),
  ], [files.map((f) => f.name).join(","), entry, envValues, egressText]);

  const next = {
    label: submitLabel,
    icon: "solar:link-circle-linear",
    disabled: !ready,
    hint: !files.length ? "Add the folder or .zip" : "Name the entry file",
    onClick: () => onSave({
      kind: "upload",
      entry: entry.trim(),
      files: files.map((f) => ({ name: f.name, size: f.size })),
      ...envValuesSourceOf({ values: envValues, files: credFiles }),
      ...(egress.length && { allowedEgressDomains: egress }),
      /* Built in the hosted sandbox — nothing to reach. */
      runtime: { endpoint: "", location: "hosted" },
      runLocation: "hosted",
      name: name.trim(),
      versionNote: `Uploaded ${files.length} file${files.length === 1 ? "" : "s"}.`,
    }),
  };

  return (
    <ConnectSteps
      title={title}
      subtitle="A folder or a .zip, read once and built in our sandbox for every run. Fixes come back as a patch to apply."
      steps={STEPS}
      active={0}
      next={next}
      preflight={{
        state: preflight,
        canRun: ready,
        blockedReason: !ready ? "Add the files and name the entry file — then preflight checks them and their credentials." : null,
      }}
    >
      <Stack spacing={1.75}>
        <Box
          onDragOver={(e) => { e.preventDefault(); setOver(true); }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer?.files?.length) add(e.dataTransfer.files); }}
          sx={{
            display: "flex", alignItems: "center", gap: 1.5, px: 1.75, py: 1.5,
            border: "1px dashed", borderColor: over ? "text.primary" : "divider", borderRadius: 1,
            bgcolor: (t) => (over ? alpha(t.palette.text.primary, 0.05) : "transparent"),
            transition: "background-color .12s ease, border-color .12s ease",
          }}
        >
          <Iconify icon="solar:cloud-upload-linear" width={20} sx={{ color: "text.subtitle", flexShrink: 0 }} />
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>{over ? "Drop to upload" : "Drop a folder or a .zip here"}</Typography>
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>Up to 200 MB</Typography>
          </Box>
          <input ref={inputRef} type="file" hidden multiple accept=".zip,.tar,.gz,.tgz,.py,.ts,.js,.json,.yaml,.yml,.md,.toml,.txt" onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          <Button
            size="small" variant="outlined" onClick={() => inputRef.current?.click()}
            startIcon={<Iconify icon="solar:upload-minimalistic-linear" width={14} />}
            sx={{ typography: "s3", fontWeight: 700, color: "text.primary", borderColor: "divider", height: 28, flexShrink: 0 }}
          >
            Choose files
          </Button>
        </Box>

        {files.length > 0 && (
          <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
            {files.map((f, i) => (
              <Stack key={f.name} direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 0.625, borderTop: i ? "1px solid" : "none", borderColor: "divider" }}>
                <Iconify icon="solar:document-linear" width={13} sx={{ color: "text.subtitle", flexShrink: 0 }} />
                <Typography noWrap sx={{ typography: "s2", fontFamily: MONO, flex: 1, minWidth: 0 }}>{f.name}</Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle", fontVariantNumeric: "tabular-nums" }}>{sizeOf(f.size)}</Typography>
                <IconButton size="small" aria-label={`Remove ${f.name}`} onClick={() => setFiles((prev) => prev.filter((x) => x.name !== f.name))}>
                  <Iconify icon="solar:trash-bin-minimalistic-linear" width={13} sx={{ color: "text.subtitle" }} />
                </IconButton>
              </Stack>
            ))}
          </Box>
        )}

        {files.length > 0 && (
          <Field
            label="Entry file" required mono
            placeholder="app/chat.py:handle_message"
            value={entry} onChange={setEntry}
            helper={<HintLine hint="Inside a .zip, the path from its root. Add :function when the agent doesn't start at the file's main.">The file the agent starts in.</HintLine>}
          />
        )}

        {/* The agent's code is known: what it reads, to fill. */}
        {ready && (
          <EnvironmentValues
            values={envValues} onValues={setEnvValues}
            files={credFiles} onFiles={setCredFiles}
            expectedNames={expectedNames}
            egress={egressText} onEgress={setEgressText}
          />
        )}

        {files.length > 0 && <Field label="Name" placeholder="Optional — e.g. Returns line agent" value={name} onChange={setName} />}
      </Stack>
    </ConnectSteps>
  );
}

UploadAgentFlow.propTypes = {
  title: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  /* { files } — dropped on New agent's upload area. */
  initial: PropTypes.object,
};
