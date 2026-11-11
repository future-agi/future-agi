import { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, MenuItem, Stack, TextField, Typography } from "@mui/material";
import ModalWrapper from "src/components/ModalWrapper";
import { useCreateContextBinding } from "src/api/skills/context-consumers";
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

export default function ContextTargetRegistration({ contextId, canRegister }) {
  const create = useCreateContextBinding(contextId);
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
      const value = JSON.parse(await chosen.text());
      if (!value || typeof value !== "object" || Array.isArray(value))
        throw new Error("not an object");
      setManifest(value);
    } catch {
      setError("The manifest is not a JSON object. Choose the file your build produced.");
    }
  };
  const valid =
    name.trim().length > 0 &&
    /^[a-z0-9][a-z0-9-]{0,39}$/.test(label) &&
    manifest !== null &&
    !create.isPending;
  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center">
        <Button
          variant="outlined"
          disabled={!canRegister}
          onClick={() => {
            reset();
            setDone(null);
            setOpen(true);
          }}
        >
          Register application target
        </Button>
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
        subTitle="Releases can be reviewed against this pinned runtime configuration. Registering does not move any label."
        actionBtnTitle="Register target"
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
            label="Follows label"
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            inputProps={{ maxLength: 40 }}
          />
          <TextField
            select
            size="small"
            label="Update mode"
            value={updateMode}
            onChange={(event) => setUpdateMode(event.target.value)}
          >
            <MenuItem value="new_thread">New threads only</MenuItem>
            <MenuItem value="manual">Manual</MenuItem>
          </TextField>
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
          {manifest &&
            SUMMARY.map(([key, title]) => (
              <IdentityValue
                key={key}
                label={title}
                value={typeof manifest[key] === "string" ? manifest[key] : "Missing"}
              />
            ))}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </ModalWrapper>
    </>
  );
}
ContextTargetRegistration.propTypes = {
  contextId: PropTypes.string.isRequired,
  canRegister: PropTypes.bool,
};
