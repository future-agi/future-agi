import React, { useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Typography from "@mui/material/Typography";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";

import axios, { endpoints } from "src/utils/axios";
import { paths } from "src/routes/paths";
import { RouterLink } from "src/routes/components";

import { draftBody, workbenchModelFor } from "./handoff";

// There is one playground: the Prompts workbench. The gateway's playground URL
// hands off to it:
//
//   /dashboard/gateway/playground?model=<gateway model id>&prompt=<text>&system=<text>
//
// With a model it creates a prompt (the workbench's own "create draft" call,
// as Prompts > New prompt does) with that model and the messages preset, and
// opens it. Without one it opens the Prompts list. The Prompts model list is
// the platform catalog (/model-hub/api/models_list/) and the workbench runs
// prompts with the keys in Settings > AI Providers, not through the gateway:
// a gateway model that is not in that list opens unselected, and the user is
// told so.

const PROMPTS_LIST = `${paths.dashboard.workbench}/all`;

async function openInPrompts({ model, prompt, system }) {
  const slash = model.indexOf("/");
  const search = slash < 0 ? model : model.slice(slash + 1);
  let rows = [];
  try {
    const res = await axios.get(endpoints.develop.modelList, {
      params: { page: 1, search },
    });
    rows = res.data?.results || [];
  } catch {
    rows = [];
  }
  const row = workbenchModelFor(model, rows);
  const res = await axios.post(
    endpoints.develop.runPrompt.createPromptDraft,
    draftBody(row, { prompt, system }),
  );
  const id = res.data?.result?.root_template;
  if (!id) throw new Error("The new prompt has no id");
  return { id, matched: !!row };
}

export default function PlaygroundHandoff() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const model = params.get("model") || "";
  const prompt = params.get("prompt") || "";
  const system = params.get("system") || "";
  const [failed, setFailed] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    // One prompt per visit, even when React runs effects twice.
    if (!model || started.current) return;
    started.current = true;
    openInPrompts({ model, prompt, system })
      .then(({ id, matched }) => {
        if (!matched) {
          enqueueSnackbar(
            `${model} is not in the Prompts model list. Pick a model in the playground; Prompts runs with the keys in Settings > AI Providers.`,
            { variant: "info" },
          );
        }
        navigate(`${paths.dashboard.workbench}/create/${id}`, {
          replace: true,
        });
      })
      .catch(() => setFailed(true));
  }, [model, navigate, prompt, system]);

  if (!model) return <Navigate to={PROMPTS_LIST} replace />;

  return (
    <Box
      role="status"
      sx={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1.5,
        p: 2,
        minHeight: 320,
        textAlign: "center",
      }}
    >
      {failed ? (
        <>
          <Typography typography="m3" fontWeight="fontWeightMedium">
            Could not open the Prompts playground
          </Typography>
          <Typography typography="s1" color="text.secondary">
            The prompt for {model} was not created. Open Prompts and start one
            there.
          </Typography>
          <Button component={RouterLink} href={PROMPTS_LIST} variant="outlined">
            Open Prompts
          </Button>
        </>
      ) : (
        <>
          <CircularProgress size={24} />
          <Typography typography="s1" color="text.secondary">
            Opening {model} in the Prompts playground
          </Typography>
        </>
      )}
    </Box>
  );
}
