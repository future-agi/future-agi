import PropTypes from "prop-types";
import React, { useEffect, useMemo } from "react";
import { useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useConfigureEvalStore } from "../states";
import { useDevelopDetailContext } from "../Context/DevelopDetailContext";
import { useDatasetColumnConfig } from "src/api/develop/develop-detail";
import { useSearchParams } from "react-router-dom";
import EvalPickerDrawer from "src/sections/common/EvalPicker/EvalPickerDrawer";
import { enqueueSnackbar } from "notistack";

const EditEvaluation = ({
  module = "dataset-update",
  onSuccess = () => {},
}) => {
  const { dataset: paramId, experimentId } = useParams();
  const [searchParams] = useSearchParams();
  const isExperimentPage = module === "update-experiment";
  const dataset = paramId ?? searchParams.get("datasetId");

  const { configureEval, setConfigureEval } = useConfigureEvalStore();
  const queryClient = useQueryClient();

  const onClose = () => {
    setConfigureEval(null);
  };

  const {
    isPending,
    data: evalConfig,
    isError,
  } = useQuery({
    queryKey: ["develop", "eval-template-config", configureEval?.id],
    queryFn: () =>
      axios.get(
        endpoints.develop.eval.getPreviouslyConfiguredEvalTemplateConfig(
          dataset,
          configureEval?.id,
        ),
        { params: { eval_type: "user" } },
      ),
    select: (d) => {
      if (d.data?.status === false) return null;
      return d.data?.result?.eval;
    },
    enabled: !!configureEval?.id,
    retry: false,
  });

  useEffect(() => {
    if (!isPending && (isError || evalConfig === null)) {
      onClose();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPending, isError, evalConfig]);

  const allColumns = useDatasetColumnConfig(
    dataset,
    false,
    isExperimentPage,
    isExperimentPage,
  );

  const { refreshGrid } = useDevelopDetailContext();

  const { mutateAsync: editEval } = useMutation({
    mutationFn: (payload) =>
      axios.post(
        endpoints.develop.eval.editEval(dataset, configureEval?.id),
        payload,
      ),
    onSuccess: () => {
      // Invalidate both dataset- and experiment-scoped eval caches; React
      // Query treats these as prefix matches, so subkeys by id/payload also
      // get flushed. Covers dataset edits + experiment-scope edits via the
      // same handler.
      queryClient.invalidateQueries({
        queryKey: ["develop", "user-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["experiment", "user-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["develop", "previously_configured-eval-list"],
      });
      queryClient.invalidateQueries({
        queryKey: ["optimize-develop-column-info"],
      });
      if (isExperimentPage && experimentId) {
        queryClient.invalidateQueries({
          queryKey: ["experiment", experimentId],
        });
      }
    },
    onError: (err) => {
      const msg =
        err?.response?.data?.result ||
        err?.message ||
        "Failed to update evaluation";
      enqueueSnackbar(typeof msg === "string" ? msg : JSON.stringify(msg), {
        variant: "error",
      });
    },
  });

  // Build initialEval in the shape EvalPickerDrawer / EvalPickerConfigFull expects
  const initialEval = useMemo(() => {
    if (!evalConfig) return null;
    const tplId =
      evalConfig.template_id ||
      evalConfig.templateId ||
      evalConfig.eval_template_id;
    if (!tplId) return null;
    return {
      id: tplId,
      // Use the saved user eval instance name (not the template name) so the
      // edit form pre-fills with the correct name and the edit endpoint
      // receives the right value.
      name: evalConfig.name,
      evalType: evalConfig.eval_type || evalConfig.evalType,
      // API returns saved model as selected_model; fall back to model field
      model: evalConfig.selected_model || evalConfig.model,
      // run_config may be top-level, camelCase, or nested under config
      run_config:
        evalConfig.run_config ||
        evalConfig.runConfig ||
        evalConfig.config?.run_config ||
        {},
      config: {
        required_keys:
          evalConfig.eval_required_keys ||
          evalConfig.evalRequiredKeys ||
          evalConfig.requiredKeys ||
          [],
        ...(evalConfig.run_config ||
          evalConfig.runConfig ||
          evalConfig.config?.run_config ||
          {}),
      },
      mapping: evalConfig.mapping || evalConfig.config?.mapping || {},
      outputType: evalConfig.output_type || evalConfig.outputType,
      userEvalId: evalConfig.id,
    };
  }, [evalConfig]);

  const handleEvalAdded = async (cfg) => {
    // Build run_config from EvalPickerConfigFull output. The picker emits
    // snake_case keys (pass_threshold, agent_mode, ...); read snake first
    // and keep camelCase as a defensive fallback for any legacy caller.
    const runConfig = {};
    const model = cfg.model;
    const agentMode = cfg.agent_mode ?? cfg.agentMode;
    const checkInternet = cfg.check_internet ?? cfg.checkInternet;
    const summary = cfg.summary;
    const dataInjection = cfg.data_injection ?? cfg.dataInjection;
    const knowledgeBases = cfg.knowledge_bases ?? cfg.knowledgeBases;
    const tools = cfg.tools;
    const passThreshold = cfg.pass_threshold ?? cfg.passThreshold;
    const choiceScores = cfg.choice_scores ?? cfg.choiceScores;
    const multiChoice = cfg.multi_choice ?? cfg.multiChoice;
    const errorLocalizerEnabled =
      cfg.error_localizer_enabled ?? cfg.errorLocalizerEnabled;

    if (model) runConfig.model = model;
    if (agentMode) runConfig.agent_mode = agentMode;
    if (checkInternet !== undefined) runConfig.check_internet = !!checkInternet;
    if (summary) runConfig.summary = summary;
    if (dataInjection) runConfig.data_injection = dataInjection;
    if (knowledgeBases) runConfig.knowledge_bases = knowledgeBases;
    if (tools) runConfig.tools = tools;
    if (passThreshold !== undefined) runConfig.pass_threshold = passThreshold;
    if (choiceScores && Object.keys(choiceScores).length)
      runConfig.choice_scores = choiceScores;
    if (multiChoice !== undefined) runConfig.multi_choice = !!multiChoice;
    if (errorLocalizerEnabled !== undefined)
      runConfig.error_localizer_enabled = !!errorLocalizerEnabled;

    const payload = {
      run: false,
      name: cfg.name,
      config: {
        mapping: cfg.mapping || {},
        config: cfg.config || {},
        ...(Object.keys(runConfig).length ? { run_config: runConfig } : {}),
      },
      // Experiment evals have source_id=experiment.id, not dataset_id.
      // Pass experiment_id so the backend looks up the right UserEvalMetric
      // instead of 404ing against the dataset-scoped record.
      ...(isExperimentPage && experimentId
        ? { experiment_id: experimentId }
        : {}),
    };

    await editEval(payload);
    enqueueSnackbar("Evaluation updated successfully", { variant: "success" });

    if (module === "dataset-update") {
      refreshGrid?.(null, true);
    } else {
      onSuccess?.();
    }
    onClose();
  };

  return (
    <EvalPickerDrawer
      open={Boolean(configureEval)}
      onClose={onClose}
      initialEval={isPending ? null : initialEval}
      source="dataset"
      sourceId={dataset || ""}
      sourceColumns={allColumns || []}
      existingEvals={[]}
      onEvalAdded={handleEvalAdded}
    />
  );
};

EditEvaluation.propTypes = {
  module: PropTypes.string,
  onSuccess: PropTypes.func,
};

export default EditEvaluation;
