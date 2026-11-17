import React, { useCallback, useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, CircularProgress, TextField, Typography } from "@mui/material";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { copyText } from "../models/copyText";
import { requestFailureFacts } from "../components/requestDiagnostics";
import { boundedHandoffRequest, copyableMessages, draftBody, PROMPTS_EXECUTOR_NOTICE } from "./handoff";
import { resolvePromptsModel } from "./resolvePromptsModel";

// The existing gateway URL carries user-entered messages to the separate
// Prompts executor. No inference is sent here and nothing is stored locally.
const PROMPTS_LIST = `${paths.dashboard.workbench}/all`;

function PromptHandoff({ model, prompt, system }) {
  const navigate = useNavigate();
  const [state, setState] = useState({ phase: "lookup" });
  const started = useRef(false);
  const creating = useRef(false);
  const lookingUp = useRef(false);
  const mounted = useRef(false);

  const create = useCallback(async (row) => {
    // One POST per visit, including StrictMode effect replay and double clicks.
    // Never clear this lock after an uncertain result.
    if (!mounted.current || creating.current) return;
    creating.current = true;
    setState({ phase: "creating" });
    try {
      const res = await boundedHandoffRequest((options) => axios.post(
        endpoints.develop.runPrompt.createPromptDraft,
        draftBody(row, { prompt, system }),
        options,
      ));
      const id = res.data?.result?.root_template;
      if (typeof id !== "string" || !id.trim() || res.data?.status === false) {
        throw new Error("The draft response did not confirm its id");
      }
      if (!mounted.current) return;
      if (!row) {
        enqueueSnackbar(`${model} is not in the Prompts model list. Pick a model in the playground; ${PROMPTS_EXECUTOR_NOTICE}`, { variant: "info" });
      }
      navigate(`${paths.dashboard.workbench}/create/${encodeURIComponent(id)}`, { replace: true });
    } catch (error) {
      if (!mounted.current) return;
      const facts = requestFailureFacts(error);
      setState({ phase: facts.outcome === "refused" ? "refused" : "unknown" });
    }
  }, [model, navigate, prompt, system]);

  const lookup = useCallback(async (createOnMatch) => {
    if (lookingUp.current || creating.current) return;
    lookingUp.current = true;
    setState({ phase: "lookup" });
    try {
      const row = await resolvePromptsModel(model);
      if (!mounted.current) return;
      if (row && createOnMatch) await create(row);
      else setState({ phase: row ? "matched" : "no-match", row });
    } catch {
      if (mounted.current) setState({ phase: "lookup-failed" });
    } finally {
      lookingUp.current = false;
    }
  }, [create, model]);

  useEffect(() => {
    mounted.current = true;
    if (!started.current) {
      started.current = true;
      lookup(true);
    }
    // StrictMode reactivates this visit without issuing a second lookup/POST.
    // On actual exit, a late lookup may not start a mutation or navigate.
    return () => { mounted.current = false; };
  }, [lookup]);

  const busy = state.phase === "lookup" || state.phase === "creating";
  return (
    <Box sx={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 1.5, p: 2, minHeight: 320, width: "100%", maxWidth: 720, mx: "auto" }}>
      <Box role="status" aria-live="polite" sx={{ textAlign: "center" }}>
        {busy && <CircularProgress size={24} sx={{ mb: 1.5 }} />}
        <Typography typography="m3" fontWeight="fontWeightMedium" component="h1">
          {state.phase === "lookup" ? `Looking up ${model} in Prompts`
            : state.phase === "creating" ? "Creating your Prompts draft"
              : state.phase === "lookup-failed" ? "Could not look up the Prompts model"
                : state.phase === "unknown" ? "Draft creation outcome unknown"
                  : state.phase === "refused" ? "Prompts refused draft creation"
                    : state.phase === "no-match" ? `${model} is not in the Prompts model list`
                      : "Your model is available in Prompts"}
        </Typography>
      </Box>
      <Typography typography="s1" color="text.secondary" sx={{ textAlign: "center" }}>{PROMPTS_EXECUTOR_NOTICE}</Typography>
      {state.phase === "lookup-failed" && (
        <>
          <Typography typography="s1">The Prompts model list could not be checked. No draft creation was requested. Your original messages are below. Retry only checks the model list.</Typography>
          <Button variant="contained" onClick={() => lookup(false)}>Retry lookup</Button>
        </>
      )}
      {state.phase === "unknown" && (
        <Typography typography="s1">A draft may already exist. Copy your messages and inspect existing Prompts before creating another. The console will not create it again automatically.</Typography>
      )}
      {state.phase === "refused" && (
        <Typography typography="s1">The server returned a refusal. Your messages are kept below; inspect Prompts before trying to create another draft.</Typography>
      )}
      {state.phase === "no-match" && (
        <>
          <Typography typography="s1">Continue only if you want a draft with no model selected. Choose a model and check its provider key in Prompts before running it.</Typography>
          <Button variant="outlined" onClick={() => create(null)}>Continue with model unselected</Button>
        </>
      )}
      {state.phase === "matched" && <Button variant="contained" onClick={() => create(state.row)}>Create draft in Prompts</Button>}
      {!busy && (
        <>
          <TextField label="Original system message" value={system} InputProps={{ readOnly: true }} multiline fullWidth size="small" />
          <TextField label="Original user message" value={prompt} InputProps={{ readOnly: true }} multiline fullWidth minRows={3} size="small" />
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>
            <Button variant="outlined" onClick={() => copyText(copyableMessages({ prompt, system }), "Messages copied")}>Copy messages</Button>
            <Button component={RouterLink} href={PROMPTS_LIST} variant="outlined">Inspect existing Prompts</Button>
          </Box>
        </>
      )}
    </Box>
  );
}
PromptHandoff.propTypes = { model: PropTypes.string.isRequired, prompt: PropTypes.string.isRequired, system: PropTypes.string.isRequired };

export default function PlaygroundHandoff() {
  const [params] = useSearchParams();
  const model = params.get("model") || "";
  const prompt = params.get("prompt") || "";
  const system = params.get("system") || "";
  if (!model) return <Navigate to={PROMPTS_LIST} replace />;
  // A different URL is a different visit; old pending responses cannot mutate it.
  return <PromptHandoff key={JSON.stringify([model, prompt, system])} model={model} prompt={prompt} system={system} />;
}
