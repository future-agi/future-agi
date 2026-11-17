import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Box,
  Button,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { LoadingButton } from "@mui/lab";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import SvgColor from "src/components/svg-color";
import CustomModelDropdown from "src/components/custom-model-dropdown/CustomModelDropdown";
import { getRequestErrorMessage } from "src/utils/errorUtils";
import RunCardShell from "./RunCardShell";
import { useExperimentForm } from "./ExperimentFormContext";
import {
  defaultPromptName,
  firstPromptBody,
  firstPromptIssues,
  insertToken,
  savedPromptOf,
  starterMessage,
  unknownVariables,
  unknownVariablesLine,
  variableToken,
} from "./firstPromptModel";

/**
 * Write a prompt in the New experiment wizard (JRN-EXP-NO-PROMPT-DEADEND).
 * Opened from the Prompt picker when the workspace has no saved prompt: a
 * name, a model, optional instructions and the message, with the dataset's
 * columns one click away as `{{variables}}`. Save stores it as a real prompt
 * (firstPromptModel.js says why) and hands it back as a run, the same card a
 * saved prompt makes. Nothing is sent until it can run on this dataset.
 */
const FirstPromptComposer = ({ onSaved, onCancel }) => {
  const { mappingColumns, datasetName, inputColumn } = useExperimentForm();
  const columns = useMemo(
    () => (mappingColumns ?? []).filter((c) => String(c?.name ?? "").trim()),
    [mappingColumns],
  );
  const id = useId();
  const [name, setName] = useState(() => defaultPromptName(datasetName));
  const [model, setModel] = useState(null);
  const [instructions, setInstructions] = useState("");
  const [message, setMessage] = useState(() => starterMessage(inputColumn));
  const [tried, setTried] = useState(false);
  const instructionsRef = useRef(null);
  const messageRef = useRef(null);
  // The field a column chip writes into: the last one the user was in.
  const lastField = useRef("message");
  const queryClient = useQueryClient();

  const save = useMutation({
    mutationFn: async ({ body, picked }) => {
      const res = await axios.post(
        endpoints.develop.runPrompt.createPromptDraft,
        body,
      );
      const saved = savedPromptOf(res?.data?.result, picked, body.name);
      if (!saved) throw new Error("The prompt was not saved. Try again.");
      return saved;
    },
    // The card says why a save failed; no second, generic toast.
    meta: { errorHandled: true },
    onSuccess: (saved) => {
      // The Prompt picker lists it from now on.
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
      onSaved(saved);
    },
  });
  const busy = save.isPending;

  // Start in the message once the picker that opened this has closed.
  useEffect(() => {
    const t = setTimeout(() => {
      const el = messageRef.current;
      if (!el || typeof el.focus !== "function") return;
      el.focus();
      const end = String(el.value ?? "").length;
      el.setSelectionRange?.(end, end);
    }, 250);
    return () => clearTimeout(t);
  }, []);

  const missing = firstPromptIssues({ name, model, message });
  const unknown = unknownVariables([instructions, message], columns);

  const insertColumn = (column) => {
    const inInstructions = lastField.current === "instructions";
    const el = inInstructions ? instructionsRef.current : messageRef.current;
    const next = insertToken(
      inInstructions ? instructions : message,
      variableToken(column.name),
      el?.selectionStart,
      el?.selectionEnd,
    );
    if (inInstructions) setInstructions(next.text);
    else setMessage(next.text);
    // Keep typing where the column went in.
    window.requestAnimationFrame?.(() => {
      el?.focus?.();
      el?.setSelectionRange?.(next.caret, next.caret);
    });
  };

  const submit = () => {
    setTried(true);
    if (busy || missing.length || unknown.length) return;
    save.mutate({
      body: firstPromptBody({ name, model, instructions, message }),
      picked: model,
    });
  };

  return (
    <RunCardShell
      icon={
        <SvgColor
          src="/assets/icons/navbar/ic_prompt.svg"
          sx={{ width: 16, height: 16, color: "text.secondary" }}
        />
      }
      title="New prompt"
      typeLabel="Prompt"
    >
      <Stack spacing={1.25} data-testid="first-prompt-composer">
        <Typography typography="s2" color="text.secondary">
          Write what to send for each row. Click a column to add it; each row
          fills in its own value. Saving adds it to Prompts and to this
          experiment as a run.
        </Typography>
        <TextField
          id={`${id}-name`}
          size="small"
          fullWidth
          label="Prompt name"
          value={name}
          disabled={busy}
          error={tried && !name.trim()}
          onChange={(e) => setName(e.target.value)}
          // Enter must not submit the wizard's form from this field.
          onKeyDown={(e) => {
            if (e.key === "Enter") e.preventDefault();
          }}
          sx={{ bgcolor: "background.paper" }}
        />
        <Box
          sx={{
            border: "1px solid",
            borderColor: tried && !model ? "error.main" : "divider",
            borderRadius: 0.5,
            bgcolor: "background.paper",
            px: 1.5,
            py: 1,
          }}
        >
          <CustomModelDropdown
            isModalContainer
            hoverPlacement="bottom-start"
            buttonTitle="Pick a model"
            value={model?.model_name ?? ""}
            modelDetail={model ?? {}}
            disabledClick={busy}
            extraParams={{ model_type: "llm" }}
            onChange={(e) =>
              setModel(e?.target?.value?.model_name ? e.target.value : null)
            }
          />
        </Box>
        <TextField
          id={`${id}-instructions`}
          size="small"
          fullWidth
          multiline
          minRows={2}
          maxRows={8}
          label="Instructions (optional)"
          placeholder="e.g. You answer customer questions in two sentences."
          value={instructions}
          disabled={busy}
          inputRef={instructionsRef}
          onFocus={() => {
            lastField.current = "instructions";
          }}
          onChange={(e) => setInstructions(e.target.value)}
          sx={{ bgcolor: "background.paper" }}
        />
        <TextField
          id={`${id}-message`}
          size="small"
          fullWidth
          multiline
          minRows={3}
          maxRows={12}
          label="Message"
          placeholder={`e.g. Answer this question: ${variableToken(
            inputColumn || "question",
          )}`}
          value={message}
          disabled={busy}
          error={tried && !message.trim()}
          inputRef={messageRef}
          onFocus={() => {
            lastField.current = "message";
          }}
          onChange={(e) => setMessage(e.target.value)}
          sx={{ bgcolor: "background.paper" }}
        />
        {columns.length ? (
          <Stack
            direction="row"
            alignItems="center"
            flexWrap="wrap"
            gap={0.5}
            data-testid="first-prompt-columns"
          >
            <Typography typography="s3" color="text.secondary">
              Add a column:
            </Typography>
            {columns.map((c) => (
              <Chip
                key={c.id ?? c.name}
                size="small"
                variant="outlined"
                label={variableToken(c.name)}
                disabled={busy}
                // Keep the caret in the field the column goes into.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => insertColumn(c)}
              />
            ))}
          </Stack>
        ) : (
          <Typography typography="s3" color="text.secondary">
            This dataset has no columns to add as variables.
          </Typography>
        )}
        {unknown.length ? (
          <Typography
            typography="s2"
            color="warning.dark"
            data-testid="first-prompt-unknown"
          >
            {unknownVariablesLine(unknown)}
          </Typography>
        ) : null}
        {tried && missing.length ? (
          <Typography
            typography="s2"
            color="error.main"
            data-testid="first-prompt-issues"
          >
            {missing.join(" · ")}
          </Typography>
        ) : null}
        {save.isError ? (
          <Alert severity="error">
            {getRequestErrorMessage(
              save.error,
              "Couldn't save the prompt. Try again.",
            )}
          </Alert>
        ) : null}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="flex-end"
          gap={1}
        >
          <Button
            size="small"
            color="inherit"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </Button>
          <LoadingButton
            size="small"
            variant="contained"
            color="primary"
            loading={busy}
            onClick={submit}
          >
            Save and add run
          </LoadingButton>
        </Stack>
      </Stack>
    </RunCardShell>
  );
};

FirstPromptComposer.propTypes = {
  onSaved: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
};

export default FirstPromptComposer;
