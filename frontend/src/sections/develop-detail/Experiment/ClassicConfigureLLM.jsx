import React, { useState } from "react";
import PropTypes from "prop-types";
import { Button, Stack, Typography } from "@mui/material";
import { useWatch } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import { CLASSIC_EXPERIMENT_COPY as COPY } from "src/launch/classicExperimentCopy";
import { PROMPT_CONFIG_TYPE } from "./constants";
import RunTargetPicker from "./RunTargetPicker";
import AgentPromptRenderer from "./StepperComponentExperiment/AgentPromptRenderer";

export const promptCard = (p) => ({
  _itemId: getRandomId(),
  experimentType: "llm",
  targetType: PROMPT_CONFIG_TYPE.PROMPT,
  promptId: p?.id ?? "",
  promptVersion: null,
  agentId: null,
  agentVersion: null,
  model: [
    {
      id: getRandomId(),
      value: p?.model,
      logoUrl: p?.model_detail?.logo_url ?? "",
      providers: p?.model_detail?.providers ?? "",
    },
  ],
  configuration: { toolChoice: "auto", tools: [] },
  modelParams: {},
  name: p?.name || "Untitled",
  outputFormat: "string",
  isDraft: false,
  variables: [],
  variableMapping: {},
});

// v1.56.1 prompt selection, without the new SourcePicker/catalog, keyboard
// handler, recommendations, reserved run IDs, external sources or quick tests.
export default function ClassicConfigureLLM({
  control,
  setValue,
  getValues,
  watch,
  unregister,
  showErrors = false,
  isEditing = false,
}) {
  const cards = useWatch({ control, name: "promptConfig" }) ?? [];
  const [anchor, setAnchor] = useState(null);
  const replace = (next) =>
    setValue("promptConfig", next, { shouldDirty: true });
  return (
    <Stack spacing={1.5}>
      {!isEditing && (
        <Button variant="outlined" onClick={(e) => setAnchor(e.currentTarget)}>
          {COPY.selectPrompt}
        </Button>
      )}
      <Stack spacing={0.5}>
        <Button disabled>{COPY.agentLabel}</Button>
        <Typography typography="s2" color="text.secondary">
          {COPY.agentUnavailable}
        </Typography>
      </Stack>
      {showErrors && !cards.length && (
        <Typography color="error.main">{COPY.promptRequired}</Typography>
      )}
      {cards.map((card, index) =>
        (card.targetType && card.targetType !== PROMPT_CONFIG_TYPE.PROMPT) ||
        card.agentId ||
        card.r4 ? (
          <Typography key={card._itemId ?? index} color="text.secondary">
            {COPY.sourceUnavailable}
          </Typography>
        ) : (
          <AgentPromptRenderer
            key={`${card._itemId ?? card.promptId}-${index}`}
            prompt={card}
            index={index}
            control={control}
            setValue={setValue}
            getValues={getValues}
            watch={watch}
            unregister={unregister}
            fieldPrefix={`promptConfig.${index}`}
            type={PROMPT_CONFIG_TYPE.PROMPT}
            showErrors={showErrors}
            onRemove={
              isEditing
                ? undefined
                : () => replace(cards.filter((_, i) => i !== index))
            }
          />
        ),
      )}
      <RunTargetPicker
        anchorEl={anchor}
        type={PROMPT_CONFIG_TYPE.PROMPT}
        onClose={() => setAnchor(null)}
        onPick={(p) => {
          replace([...cards, promptCard(p)]);
          setAnchor(null);
        }}
      />
    </Stack>
  );
}
ClassicConfigureLLM.propTypes = {
  control: PropTypes.object,
  setValue: PropTypes.func,
  getValues: PropTypes.func,
  watch: PropTypes.func,
  unregister: PropTypes.func,
  showErrors: PropTypes.bool,
  isEditing: PropTypes.bool,
};
