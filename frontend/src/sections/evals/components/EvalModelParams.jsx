import PropTypes from "prop-types";
import React, { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { useQuery } from "@tanstack/react-query";
import CustomModelOptions from "src/components/custom-model-options/CustomModelOptions";
import axios, { endpoints } from "src/utils/axios";
import {
  getModelParamValues,
  transformModelParams,
} from "src/sections/workbench/createPrompt/Playground/common";
import {
  normalizeConfigurationForLoad,
  normalizeConfigurationForSave,
} from "src/sections/workbench/createPrompt/common";

/**
 * Judge-model parameter popover for LLM-as-a-Judge eval config.
 *
 * Same params UI as the Prompt Workbench (CustomModelOptions), minus the
 * workbench-only concerns: no prompt versions, no Mixpanel prompt events,
 * and no responseFormat control — the judge pipeline owns the output
 * contract, so exposing a format toggle here could break score parsing.
 *
 * Values only leave this component on Apply (`onChange`), never on
 * close/cancel. `value` and the `onChange` payload use backend snake_case
 * keys (temperature, max_tokens, top_p, …) — the same names
 * run_prompt.py reads from run_config — while the form itself holds the
 * camelCase slider ids `transformModelParams` produces.
 */
export default function EvalModelParams({
  model,
  value,
  onChange,
  disabled = false,
}) {
  const {
    control,
    getValues,
    setValue,
    reset,
    formState: { isDirty },
  } = useForm();

  const { data, isError } = useQuery({
    // The endpoint requires a provider but ignores it for model_type "llm"
    // (get_model_parameters dispatches straight to get_llm_parameters), so
    // a placeholder satisfies validation for BYOK and FAGI models alike.
    queryKey: ["eval-model-params-defs", model],
    queryFn: () =>
      axios.get(endpoints.develop.modelParams, {
        params: { model, provider: "default", model_type: "llm" },
      }),
    enabled: Boolean(model),
    staleTime: 60000,
  });

  const transformed = useMemo(() => {
    if (!data?.data?.result) return null;
    const { responseFormat, ...params } = transformModelParams(
      data.data.result,
    );
    return params;
  }, [data]);

  const seeded = useMemo(
    () =>
      getModelParamValues(
        transformed || {},
        normalizeConfigurationForLoad(value || {}) || {},
      ),
    [transformed, value],
  );

  useEffect(() => {
    reset({ config: seeded });
  }, [seeded, reset]);

  const handleApply = () => {
    const config = normalizeConfigurationForSave(getValues("config")) || {};
    const applied = Object.fromEntries(
      Object.entries(config).filter(([, v]) => v !== null && v !== undefined),
    );
    onChange(Object.keys(applied).length ? applied : null);
  };

  return (
    <CustomModelOptions
      isModalContainer
      control={control}
      handleApply={handleApply}
      reset={reset}
      setValue={setValue}
      responseSchema={[]}
      modelConfig={seeded}
      isDirty={isDirty}
      disabledHover
      disabledClick={disabled || isError || !transformed}
      modelParams={transformed}
    />
  );
}

EvalModelParams.propTypes = {
  model: PropTypes.string,
  value: PropTypes.object,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
