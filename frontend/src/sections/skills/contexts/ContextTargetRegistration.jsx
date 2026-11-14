import { useId, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Link, MenuItem, Stack, TextField, Typography } from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useCreateContextBinding } from "src/api/skills/context-consumers";
import ActionReason, { reasonProps } from "../components/ActionReason";
import { IdentityValue } from "../review/ReleaseReviewBody";

// Intent: an application owner explicitly registers one runtime target with
// the pinned manifest its build produced. The server validates the manifest and
// derives its digest; nothing here is typed by hand or inferred from traffic.
const SUMMARY = [
  ["code_commit", "Code commit"],
  ["entrypoint", "Entrypoint"],
  ["image_digest", "Image digest"],
  ["runtime_version", "Runtime"],
  ["tools_digest", "Tools digest"],
  ["side_effect_policy", "Side-effect policy"],
];

export default function ContextTargetRegistration({ contextId, canRegister, variant = "outlined" }) {
  const create = useCreateContextBinding(contextId);
  const reasonId = useId();
  const file = useRef(null);
  const operationKey = useRef(null);
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("production");
  const [updateMode, setUpdateMode] = useState("new_thread");
  const [manifest, setManifest] = useState(null);
  const [manifestName, setManifestName] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);
  const reset = () => {
    setName("");
    setLabel("production");
    setUpdateMode("new_thread");
    setManifest(null);
    setManifestName("");
    setError("");
    operationKey.current = null;
  };
  const readManifest = async (chosen) => {
    setError("");
    setManifest(null);
    setManifestName(chosen?.name || "");
    if (!chosen) return;
    try {
      const text = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(chosen);
      });
      const value = JSON.parse(text);
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("not an object");
      setManifest(value);
      // J4: default the visible name from the build's entrypoint module when
      // none was typed (e.g. "refund_agent.app:invoke" -> "refund_agent").
      const derived =
        typeof value.entrypoint === "string"
          ? value.entrypoint.split(":")[0].split(".")[0].trim().slice(0, 120)
          : "";
      if (derived) setName((current) => (current.trim() ? current : derived));
    } catch {
      setError("The manifest is not a JSON object. Choose the file your build produced.");
    }
  };
  const valid =
    name.trim().length > 0 &&
    /^[a-z0-9][a-z0-9-]{0,39}$/.test(label) &&
    manifest !== null &&
    !create.isPending && canRegister;
  const actionReason = !canRegister ? 'Ask a workspace admin for permission to connect an app.'
    : !manifest ? 'Choose the app manifest (JSON) file from your app build.'
      : !name.trim() ? 'Enter a target name.'
        : !/^[a-z0-9][a-z0-9-]{0,39}$/.test(label) ? 'Use a label with lowercase letters, numbers or hyphens, starting with a letter or number.'
          : create.isPending ? 'Connecting the app…' : '';
  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center">
        <Button
          color={variant === "contained" ? "primary" : "inherit"}
          variant={variant}
          disabled={!canRegister}
          {...reasonProps(`${reasonId}-access`, !canRegister && actionReason)}
          onClick={() => {
            reset();
            setDone(null);
            setOpen(true);
          }}
        >
          Register application target
        </Button>
        {!canRegister && <ActionReason id={`${reasonId}-access`} reason={actionReason} />}
        {done && (
          <Typography variant="s3">
            Registered {done.name} · configuration r{done.revision}
          </Typography>
        )}
      </Stack>
      <ModalWrapper
        open={open}
        onClose={() => {
          if (!create.isPending) setOpen(false);
        }}
        title="Register application target"
        subTitle="Connect your app using the configuration from its build. Connecting doesn't change production."
        actionBtnTitle="Register target"
        actionBtnProps={reasonProps(`${reasonId}-submit`, actionReason)}
        isValid={valid}
        isLoading={create.isPending}
        onCancelBtn={() => setOpen(false)}
        onSubmit={async () => {
          setError("");
          operationKey.current = operationKey.current || crypto.randomUUID();
          try {
            const binding = await create.mutateAsync({
              idempotencyKey: operationKey.current,
              name: name.trim(),
              kind: "runtime",
              selector: { label },
              updateMode,
              profile: "native-runtime",
              profileVersion: "1",
              repository: null,
              expectedReportIntervalSeconds: 3600,
              configuration: manifest,
            });
            setDone(binding);
            setOpen(false);
          } catch (failure) {
            setError(
              failure?.statusCode === 422
                ? "The server rejected the manifest. It needs every pinned native field."
                : "Registration was not confirmed. Retry keeps the same operation.",
            );
          }
        }}
      >
        <Stack spacing={1.5}>
          <TextField
            size="small"
            label="Target name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            inputProps={{ maxLength: 120 }}
          />
          <TextField
            size="small"
            label="Release label to follow"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            inputProps={{ maxLength: 40 }}
          />
          <TextField
            select
            size="small"
            label="When to update"
            value={updateMode}
            onChange={(event) => setUpdateMode(event.target.value)}
          >
            <MenuItem value="new_thread">New threads only</MenuItem>
            <MenuItem value="manual">Manual</MenuItem>
          </TextField>
          <Stack spacing={0.5} role="group" aria-labelledby={`${reasonId}-manifest`}>
          <Typography id={`${reasonId}-manifest`} variant="s1" sx={{ fontWeight: 500, color: "text.primary" }}>App manifest (JSON)</Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <Button variant="outlined" onClick={() => file.current?.click()}>
              Choose runtime manifest
            </Button>
            <Typography variant="s3" sx={{ overflowWrap: "anywhere" }}>
              {manifestName || "No manifest chosen"}
            </Typography>
            <input
              ref={file}
              hidden
              type="file"
              accept="application/json,.json"
              aria-label="Runtime manifest file"
              onChange={(event) => readManifest(event.target.files?.[0])}
            />
          </Stack>
          <Typography variant="s3" color="text.secondary">Choose the JSON file your app build produces. It is the manifest your app reports on startup. <Link href="https://docs.futureagi.com/docs/skills/reviewed-releases" target="_blank" rel="noopener noreferrer">Runtime setup guide</Link></Typography>
          </Stack>
          {manifest &&
            SUMMARY.map(([key, title]) => (
              <IdentityValue
                key={key}
                label={title}
                value={typeof manifest[key] === "string" ? manifest[key] : "Missing"}
              />
            ))}
          <ActionReason id={`${reasonId}-submit`} reason={actionReason} />
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </ModalWrapper>
    </>
  );
}
ContextTargetRegistration.propTypes = {
  contextId: PropTypes.string.isRequired,
  canRegister: PropTypes.bool,
  variant: PropTypes.oneOf(["contained", "outlined"]),
};
