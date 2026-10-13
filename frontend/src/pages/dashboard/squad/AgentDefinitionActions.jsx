import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Stack } from "@mui/material";
import {
  exportAgentDefinition,
  importAgentDefinition,
  importAgentDefinitionZip,
  MAX_DEFINITION_BYTES,
} from "./agentDefinition";

export default function AgentDefinitionActions({
  agentId,
  client,
  canCreate,
  disabled,
  onDraft,
  importRequest = 0,
}) {
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const fileInput = useRef(null);
  const lifetime = useRef(null);
  useEffect(() => {
    const scope = { client, agentId };
    lifetime.current = scope;
    setBusy(false);
    setMessage("");
    setError("");
    return () => {
      if (lifetime.current === scope) lifetime.current = null;
    };
  }, [client, agentId]);
  useEffect(() => {
    if (importRequest > 0 && canCreate && !disabled && !busy)
      fileInput.current?.click();
  }, [importRequest, canCreate, disabled, busy]);
  const permissions = useRef({ canCreate, disabled });
  permissions.current = { canCreate, disabled };

  async function withDefinition(action) {
    if (!agentId || disabled || busy || (action === "duplicate" && !canCreate))
      return;
    const scope = lifetime.current;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const response = await client.getWorkforceAgent(agentId);
      if (
        !scope ||
        lifetime.current !== scope ||
        permissions.current.disabled ||
        (action === "duplicate" && !permissions.current.canCreate)
      )
        return;
      if (!response?.available || response.agent?.id !== agentId)
        throw new Error(
          response?.reason ||
            "The complete agent definition could not be read. Refresh and try again.",
        );
      const serialized = exportAgentDefinition(response.agent);
      if (action === "duplicate") {
        onDraft(importAgentDefinition(serialized), true);
        setMessage(
          "Review the copy, select its environment, and authorize its tools before running it.",
        );
      } else {
        const blob = new Blob([serialized], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `${response.agent.name.replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 120) || "agent"}.omega-agent.json`;
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setMessage(
          "Definition exported. Connections and environment must be configured after import.",
        );
      }
    } catch (err) {
      if (lifetime.current === scope)
        setError(err.message || "The definition could not be exported.");
    } finally {
      if (lifetime.current === scope) setBusy(false);
    }
  }

  async function readDefinition(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || busy || disabled || !canCreate) return;
    const scope = lifetime.current;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      if (file.size > MAX_DEFINITION_BYTES)
        throw new Error("Choose a definition no larger than 256 KiB.");
      const readArrayBuffer = () =>
        file.arrayBuffer
          ? file.arrayBuffer()
          : new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(reader.result);
              reader.onerror = () =>
                reject(new Error("The selected file could not be read."));
              reader.readAsArrayBuffer(file);
            });
      const portable = file.name.toLowerCase().endsWith(".zip")
        ? importAgentDefinitionZip(new Uint8Array(await readArrayBuffer()))
        : importAgentDefinition(
            await new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result));
              reader.onerror = () =>
                reject(new Error("The selected file could not be read."));
              reader.readAsText(file);
            }),
          );
      if (
        !scope ||
        lifetime.current !== scope ||
        !permissions.current.canCreate ||
        permissions.current.disabled
      )
        return;
      onDraft(portable, false);
      setMessage(
        "Review the imported definition, select its environment, and authorize its tools before running it.",
      );
    } catch (err) {
      if (lifetime.current === scope)
        setError(err.message || "The definition could not be imported.");
    } finally {
      if (lifetime.current === scope) setBusy(false);
    }
  }

  return (
    <Stack spacing={1} sx={{ mt: 1.5 }}>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        {agentId ? (
          <Button
            size="small"
            onClick={() => void withDefinition("export")}
            disabled={disabled || busy}
          >
            Export definition
          </Button>
        ) : null}
        {agentId && canCreate ? (
          <Button
            size="small"
            onClick={() => void withDefinition("duplicate")}
            disabled={disabled || busy}
          >
            Duplicate
          </Button>
        ) : null}
        {canCreate ? (
          <>
            <input
              aria-label="Agent definition file"
              ref={fileInput}
              type="file"
              accept="application/json,.json,application/zip,.zip"
              hidden
              onChange={(event) => void readDefinition(event)}
            />
            <Button
              size="small"
              onClick={() => fileInput.current?.click()}
              disabled={disabled || busy}
            >
              Import definition or .zip
            </Button>
          </>
        ) : null}
      </Stack>
      {error ? (
        <Alert severity="error">{error}</Alert>
      ) : message ? (
        <Alert severity="info">{message}</Alert>
      ) : null}
    </Stack>
  );
}
AgentDefinitionActions.propTypes = {
  agentId: PropTypes.string,
  client: PropTypes.object.isRequired,
  canCreate: PropTypes.bool.isRequired,
  disabled: PropTypes.bool.isRequired,
  onDraft: PropTypes.func.isRequired,
  importRequest: PropTypes.number,
};
