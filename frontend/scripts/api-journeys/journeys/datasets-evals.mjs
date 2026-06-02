import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import process from "node:process";
import { promisify } from "node:util";
import {
  apiPath,
  asArray,
  assert,
  currentUserEmail,
  currentUserId,
  isUuid,
  requireMutations,
  skip,
  unwrapApiData,
} from "../lib/api-client.mjs";
import { queuePath, resolveQueue, resolveQueueItem } from "../lib/fixtures.mjs";

const execFileAsync = promisify(execFile);

export const datasetEvalJourneys = [
  {
    id: "DPE-API-001",
    title: "Dataset list and first dataset detail/table shape",
    tags: ["dataset", "safe", "smoke"],
    async run({ client, evidence }) {
      const datasets = asArray(
        await client.get(apiPath("/model-hub/develops/get-datasets/"), {
          query: { page: 0, page_size: 10 },
        }),
      );
      const dataset = datasets.find((row) => row?.id);
      if (!dataset) skip("No datasets found for this account/workspace.");

      const detail = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
          dataset_id: dataset.id,
        }),
      );
      assert(
        detail && typeof detail === "object",
        "Dataset table detail returned an empty response.",
      );

      evidence.push({
        dataset_id: dataset.id,
        dataset_name: dataset.name || detail.name || "",
      });
    },
  },
  {
    id: "DPE-API-013",
    title: "Dataset metadata, summary, provider, and run-prompt catalogs",
    tags: ["dataset", "develop", "provider", "safe", "db-audit"],
    async run({ client, evidence, organizationId, workspaceId }) {
      const listPayload = await client.get(
        apiPath("/model-hub/develops/get-datasets/"),
        {
          query: {
            page: 0,
            page_size: 10,
            sort: JSON.stringify([
              { column_id: "created_at", type: "descending" },
            ]),
          },
        },
      );
      const datasets = asArray(listPayload);
      const dataset = datasets.find((row) => row?.id && row?.name);
      if (!dataset) skip("No datasets found for metadata readback coverage.");
      assertDatasetListRow(dataset);
      assertSortedDescending(
        datasets.map((row) => row.created_at).filter(Boolean),
        "Dataset list created_at sort did not return descending order.",
      );

      const datasetId = dataset.id;
      const dbAudit = await loadDatasetMetadataDbAudit(
        datasetId,
        organizationId,
        workspaceId,
      );
      assert(
        dbAudit.dataset_id === datasetId,
        "Dataset DB audit returned a different dataset id.",
      );
      assert(
        dbAudit.organization_id === organizationId,
        "Dataset DB audit organization scope did not match request context.",
      );
      assert(
        !workspaceId || dbAudit.workspace_id === workspaceId,
        "Dataset DB audit workspace scope did not match request context.",
      );
      assert(
        Number(dataset.number_of_datapoints) ===
          Number(dbAudit.active_row_count),
        "Dataset list datapoint count did not match active DB row count.",
      );

      const [namesPayload, excludedNamesPayload, columnsPayload] =
        await Promise.all([
          client.get(apiPath("/model-hub/develops/get-datasets-names/"), {
            query: { search_text: dataset.name },
          }),
          client.get(apiPath("/model-hub/develops/get-datasets-names/"), {
            query: { search_text: dataset.name, excluded_dataset: [datasetId] },
          }),
          client.get(
            apiPath("/model-hub/dataset/columns/{dataset_id}/", {
              dataset_id: datasetId,
            }),
          ),
        ]);

      const names = payloadArray(namesPayload, "datasets");
      assert(
        names.some((row) => row.dataset_id === datasetId),
        "Dataset picker names endpoint did not include the selected dataset.",
      );
      assertDatasetNameRows(names);
      const excludedNames = payloadArray(excludedNamesPayload, "datasets");
      assert(
        !excludedNames.some((row) => row.dataset_id === datasetId),
        "Dataset picker excluded_dataset filter still returned the excluded dataset.",
      );

      const columns = payloadArray(columnsPayload, "columns");
      assertColumnRows(columns);
      assert(
        columns.length === Number(dbAudit.visible_other_column_count),
        "Dataset columns endpoint count did not match ordered active OTHERS columns in DB.",
      );

      const [
        baseColumnsPayload,
        annotationSummary,
        evalStats,
        runPromptStats,
        jsonSchema,
        explanationSummary,
        derivedDatasets,
        providerStatus,
        runPromptOptions,
        functionList,
      ] = await Promise.all([
        client.get(apiPath("/model-hub/datasets/get-base-columns/"), {
          query: { dataset_ids: [datasetId] },
        }),
        client.get(
          apiPath("/model-hub/dataset/{dataset_id}/annotation-summary/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(
          apiPath("/model-hub/dataset/{dataset_id}/eval-stats/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(
          apiPath("/model-hub/dataset/{dataset_id}/run-prompt-stats/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(
          apiPath("/model-hub/dataset/{dataset_id}/json-schema/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(
          apiPath("/model-hub/datasets/explanation-summary/{dataset_id}/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(
          apiPath("/model-hub/develops/get-derived-datasets/{dataset_id}/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(apiPath("/model-hub/develops/provider-status/")),
        client.get(apiPath("/model-hub/develops/retrieve_run_prompt_options/")),
        client.get(apiPath("/model-hub/develops/get_function_list/")),
      ]);

      const baseColumns = payloadArray(baseColumnsPayload, "base_columns");
      assert(
        baseColumns.length === Number(dbAudit.base_column_name_count),
        "Base-columns endpoint count did not match DB eligible column-name count.",
      );
      assertAnnotationSummaryShape(annotationSummary);
      assert(
        Array.isArray(evalStats),
        "Dataset eval-stats endpoint did not return an array.",
      );
      assertRunPromptStatsShape(runPromptStats);
      assert(
        Number(runPromptStats.prompts.length) <=
          Number(dbAudit.run_prompt_count),
        "Run-prompt stats returned more prompts than active DB run prompts.",
      );
      assertJsonSchemaShape(jsonSchema);
      assertExplanationSummaryShape(
        explanationSummary,
        dbAudit.active_row_count,
      );
      assert(
        Array.isArray(derivedDatasets),
        "Derived datasets endpoint did not return an array.",
      );

      const providers = payloadArray(providerStatus, "providers");
      const providerDbState = assertProviderStatusRows(providers, dbAudit);
      const runPromptModels = payloadArray(runPromptOptions, "models");
      assertRunPromptOptions(runPromptOptions, providers);
      const functions = payloadArray(functionList, "functions");
      assert(
        functions.length > 0,
        "Function list endpoint returned no eval functions.",
      );
      assertFunctionRows(functions);

      evidence.push({
        dataset_id: datasetId,
        dataset_name: dataset.name,
        rows: Number(dbAudit.active_row_count),
        columns: columns.length,
        base_columns: baseColumns.length,
        json_schema_entries: Object.keys(jsonSchema || {}).length,
        providers: providers.length,
        configured_providers: providers.filter((provider) => provider.has_key)
          .length,
        active_provider_key_rows: Number(dbAudit.provider_key_count),
        duplicate_provider_key_rows: providerDbState.duplicateProviderKeyRows,
        unsupported_provider_keys: providerDbState.unsupportedProviders,
        run_prompt_models: runPromptModels.length,
        eval_functions: functions.length,
      });
    },
  },
  {
    id: "DOPT-API-001",
    title:
      "Dataset optimization read surfaces, guards, update, and delete cascade",
    tags: ["dataset", "optimization", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      evidence,
      organizationId,
      workspaceId,
      runId,
    }) {
      requireMutations();
      let candidate = await loadDatasetOptimizationReadCandidate(
        organizationId,
        workspaceId,
      );
      let fixture = null;
      if (!candidate?.optimization_id || !candidate?.trial_id) {
        fixture = await seedDatasetOptimizationFixture({
          runId,
          organizationId,
          workspaceId,
        });
        if (!fixture?.fixture_created) {
          skip(
            "No scoped dataset column and eval metric found for disposable optimization fixture.",
          );
        }
        cleanup.defer(
          "hard delete API journey dataset optimization fixture",
          () => hardDeleteDatasetOptimizationFixture(fixture.optimization_id),
        );
        candidate = {
          optimization_id: fixture.optimization_id,
          optimization_name: fixture.optimization_name,
          status: "completed",
          column_id: fixture.column_id,
          dataset_id: fixture.dataset_id,
          trial_id: fixture.trial_id,
          step_count: 1,
          trial_count: 2,
        };
        evidence.push({
          dataset_optimization_read_fixture: "seeded",
          seeded_read_optimization_id: fixture.optimization_id,
        });
      }

      const listPayload = await client.get(
        apiPath("/model-hub/dataset-optimization/"),
        {
          query: {
            dataset_id: candidate.dataset_id,
            page: 1,
            page_size: 20,
          },
        },
      );
      const listRows = payloadArray(listPayload, "table");
      assert(
        listRows.some((row) => row.id === candidate.optimization_id),
        "Dataset optimization list did not include the selected run.",
      );
      assertDatasetOptimizationListRows(listRows);

      const optimizationPath = apiPath(
        "/model-hub/dataset-optimization/{id}/",
        {
          id: candidate.optimization_id,
        },
      );
      const [
        detail,
        steps,
        graph,
        trialDetail,
        trialPrompt,
        scenarios,
        evaluations,
      ] = await Promise.all([
        client.get(optimizationPath),
        client.get(
          apiPath("/model-hub/dataset-optimization/{id}/steps/", {
            id: candidate.optimization_id,
          }),
        ),
        client.get(
          apiPath("/model-hub/dataset-optimization/{id}/graph/", {
            id: candidate.optimization_id,
          }),
        ),
        client.get(
          apiPath("/model-hub/dataset-optimization/{id}/trial/{trial_id}/", {
            id: candidate.optimization_id,
            trial_id: candidate.trial_id,
          }),
        ),
        client.get(
          apiPath(
            "/model-hub/dataset-optimization/{id}/trial/{trial_id}/prompt/",
            {
              id: candidate.optimization_id,
              trial_id: candidate.trial_id,
            },
          ),
        ),
        client.get(
          apiPath(
            "/model-hub/dataset-optimization/{id}/trial/{trial_id}/scenarios/",
            {
              id: candidate.optimization_id,
              trial_id: candidate.trial_id,
            },
          ),
        ),
        client.get(
          apiPath(
            "/model-hub/dataset-optimization/{id}/trial/{trial_id}/evaluations/",
            {
              id: candidate.optimization_id,
              trial_id: candidate.trial_id,
            },
          ),
        ),
      ]);
      assertDatasetOptimizationDetail(detail);
      assertDatasetOptimizationSteps(steps, candidate.step_count);
      assertDatasetOptimizationGraph(graph);
      assertDatasetOptimizationTrialPayload(trialDetail);
      assertDatasetOptimizationTrialPrompt(trialPrompt);
      assertDatasetOptimizationTrialTable(scenarios);
      assertDatasetOptimizationTrialTable(evaluations);

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/dataset-optimization/{id}/stop/", {
              id: candidate.optimization_id,
            }),
            {},
          ),
        400,
        "Dataset optimization stop accepted a completed optimization.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/dataset-optimization/"), {
            name: `api journey blocked optimization ${runId}`,
            column_id: randomUUID(),
            optimizer_algorithm: "random_search",
            optimizer_config: { num_variations: 1 },
            user_eval_template_ids: [],
          }),
        400,
        "Dataset optimization create accepted an unknown column id.",
      );

      if (!fixture) {
        fixture = await seedDatasetOptimizationFixture({
          runId,
          organizationId,
          workspaceId,
        });
        if (!fixture?.fixture_created) {
          skip(
            "No scoped dataset column and eval metric found for disposable optimization fixture.",
          );
        }
        cleanup.defer(
          "hard delete API journey dataset optimization fixture",
          () => hardDeleteDatasetOptimizationFixture(fixture.optimization_id),
        );
      }

      const fixturePath = apiPath("/model-hub/dataset-optimization/{id}/", {
        id: fixture.optimization_id,
      });
      const seededDetail = await client.get(fixturePath);
      assertDatasetOptimizationDetail(seededDetail);

      const patchedName = `api_journey_opt_patch_${runId}`;
      const patched = await client.patch(fixturePath, { name: patchedName });
      assert(
        patched?.name === patchedName,
        "Dataset optimization PATCH did not return the patched name.",
      );

      const putName = `api_journey_opt_put_${runId}`;
      const putPayload = {
        name: putName,
        column: fixture.column_id,
        optimizer_algorithm: "random_search",
        optimizer_model: null,
        optimizer_config: {
          num_variations: 1,
          model_name: "gpt-4o-mini",
        },
        status: "completed",
        error_message: null,
        best_score: 0.84,
        baseline_score: 0.42,
        optimized_k_prompts: ["api journey put prompt"],
      };
      const updated = await client.put(fixturePath, putPayload);
      assert(
        updated?.name === putName && updated?.best_score === 0.84,
        "Dataset optimization PUT did not persist the expected fields.",
      );

      await client.delete(fixturePath);
      const deletedAudit = await loadDatasetOptimizationDeleteAudit(
        fixture.optimization_id,
      );
      assertDatasetOptimizationDeletedAudit(deletedAudit);

      evidence.push({
        selected_optimization_id: candidate.optimization_id,
        selected_trial_id: candidate.trial_id,
        selected_step_count: Number(candidate.step_count),
        seeded_optimization_id: fixture.optimization_id,
        seeded_dataset_id: fixture.dataset_id,
        seeded_column_id: fixture.column_id,
        seeded_metric_id: fixture.metric_id,
        deleted_child_active_counts: {
          steps: Number(deletedAudit.active_step_count),
          trials: Number(deletedAudit.active_trial_count),
          items: Number(deletedAudit.active_item_count),
          evaluations: Number(deletedAudit.active_evaluation_count),
        },
      });
    },
  },
  {
    id: "DPE-API-014",
    title: "Experiment V2 read surfaces and legacy read compatibility",
    tags: ["experiments", "safe", "db-audit"],
    async run({ client, evidence, organizationId, workspaceId }) {
      const {
        listRows,
        row: experimentRow,
        detail,
      } = await selectExperimentForReadCoverage(client);
      assertExperimentListRow(experimentRow);

      const experimentId = experimentRow.id;
      const datasetId = detail.dataset_id || experimentRow.dataset;
      assert(isUuid(experimentId), "Selected experiment id was not a UUID.");
      assert(
        isUuid(datasetId),
        "Selected experiment dataset id was not a UUID.",
      );
      assert(
        detail.id === experimentId,
        "V2 experiment detail returned a different experiment id.",
      );
      assert(
        isUuid(detail.snapshot_dataset_id),
        "V2 experiment detail did not expose a snapshot dataset id.",
      );

      const dbAudit = await loadExperimentReadDbAudit(
        experimentId,
        organizationId,
        workspaceId,
      );
      assert(
        dbAudit.experiment_id === experimentId,
        "Experiment DB audit returned a different experiment id.",
      );
      assert(
        dbAudit.organization_id === organizationId,
        "Experiment DB audit organization scope did not match request context.",
      );
      assert(
        !workspaceId || dbAudit.workspace_id === workspaceId,
        "Experiment DB audit workspace scope did not match request context.",
      );
      assert(
        dbAudit.snapshot_dataset_id === detail.snapshot_dataset_id,
        "Experiment detail snapshot dataset did not match DB.",
      );

      const legacyList = asArray(
        await client.get(apiPath("/model-hub/experiments/data/"), {
          query: { page: 1, page_size: 10 },
        }),
      );
      assert(
        legacyList.every((row) => row?.id),
        "Legacy experiment data list returned rows without ids.",
      );

      const legacyDetail = await client.get(
        apiPath("/model-hub/experiments/"),
        { query: { experiment_id: experimentId } },
      );
      assert(
        legacyDetail?.name === detail.name &&
          legacyDetail?.dataset_id === datasetId,
        "Legacy experiment detail did not match the selected experiment.",
      );

      const [rowsPayload, columnOnlyPayload, stats, comparisons, jsonSchema] =
        await Promise.all([
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/rows/", {
              experiment_id: experimentId,
            }),
            { query: { page_size: 5, current_page_index: 0 } },
          ),
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/rows/", {
              experiment_id: experimentId,
            }),
            {
              query: {
                page_size: 5,
                current_page_index: 0,
                column_config_only: true,
              },
            },
          ),
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/stats/", {
              experiment_id: experimentId,
            }),
          ),
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/comparisons/", {
              experiment_id: experimentId,
            }),
          ),
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/json-schema/", {
              experiment_id: experimentId,
            }),
          ),
        ]);
      assertExperimentRowsPayload(rowsPayload, dbAudit);
      assertExperimentColumnOnlyPayload(columnOnlyPayload, dbAudit);
      assertExperimentStatsPayload(stats);
      assertExperimentComparisonsPayload(comparisons, experimentId);
      assert(
        jsonSchema &&
          typeof jsonSchema === "object" &&
          !Array.isArray(jsonSchema),
        "Experiment JSON schema endpoint did not return an object.",
      );

      const tableRows = payloadArray(rowsPayload?.table, "table");
      const firstRow = tableRows.find((row) => isUuid(row?.row_id));
      if (firstRow) {
        const [rowDetail, legacyRowDetail] = await Promise.all([
          client.get(
            apiPath(
              "/model-hub/experiments/v2/{experiment_id}/rows/{row_id}/",
              { experiment_id: experimentId, row_id: firstRow.row_id },
            ),
            { query: { page_size: 5, current_page_index: 0 } },
          ),
          client.get(
            apiPath("/model-hub/experiments/{experiment_id}/{row_id}/", {
              experiment_id: experimentId,
              row_id: firstRow.row_id,
            }),
            { query: { page_size: 5, current_page_index: 0 } },
          ),
        ]);
        assertExperimentRowDetail(rowDetail, firstRow.row_id, "V2");
        assertExperimentRowDetail(legacyRowDetail, firstRow.row_id, "legacy");
      }

      const [
        derivedVariables,
        feedbackDetails,
        suggestName,
        duplicateName,
        uniqueName,
        v2Download,
        legacyRows,
        legacyStats,
        legacyComparisons,
        legacyDownload,
      ] = await Promise.all([
        client.get(
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/derived-variables/",
            { experiment_id: experimentId },
          ),
        ),
        client.get(
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
            { experiment_id: experimentId },
          ),
        ),
        client.get(
          apiPath("/model-hub/experiments/v2/suggest-name/{dataset_id}/", {
            dataset_id: datasetId,
          }),
        ),
        client.get(apiPath("/model-hub/experiments/v2/validate-name/"), {
          query: { dataset_id: datasetId, name: detail.name },
        }),
        client.get(apiPath("/model-hub/experiments/v2/validate-name/"), {
          query: {
            dataset_id: datasetId,
            name: `api_journey_unique_${Date.now()}`,
          },
        }),
        client.get(
          apiPath("/model-hub/experiments/v2/{experiment_id}/download/", {
            experiment_id: experimentId,
          }),
        ),
        client.get(
          apiPath("/model-hub/experiments/{experiment_id}/", {
            experiment_id: experimentId,
          }),
          { query: { page_size: 5, current_page_index: 0 } },
        ),
        client.get(
          apiPath("/model-hub/experiments/{experiment_id}/stats/", {
            experiment_id: experimentId,
          }),
        ),
        client.get(
          apiPath("/model-hub/experiments/{experiment_id}/comparisons/", {
            experiment_id: experimentId,
          }),
        ),
        client.get(
          apiPath("/model-hub/experiments/{experiment_id}/download/", {
            experiment_id: experimentId,
          }),
        ),
      ]);
      assert(
        derivedVariables?.derived_variables &&
          typeof derivedVariables.derived_variables === "object",
        "Experiment derived-variables endpoint did not return derived_variables.",
      );
      assert(
        Array.isArray(feedbackDetails?.feedback) &&
          Number.isInteger(Number(feedbackDetails.total_count)),
        "Experiment feedback details endpoint returned an unexpected shape.",
      );
      assert(
        typeof suggestName?.suggested_name === "string" &&
          suggestName.suggested_name.length > 0,
        "Experiment suggest-name endpoint did not return a suggested name.",
      );
      assert(
        duplicateName?.is_valid === false,
        "Experiment validate-name did not reject the existing experiment name.",
      );
      assert(
        uniqueName?.is_valid === true,
        "Experiment validate-name did not accept a unique experiment name.",
      );
      assert(
        typeof v2Download === "string",
        "Experiment V2 download endpoint did not return CSV text.",
      );
      assertExperimentRowsPayload(legacyRows, dbAudit);
      assertExperimentStatsPayload(legacyStats);
      assertExperimentComparisonsPayload(legacyComparisons, experimentId);
      assert(
        typeof legacyDownload === "string",
        "Legacy experiment download endpoint did not return CSV text.",
      );

      const evalMetricId = firstExperimentEvalMetricId(detail, dbAudit);
      if (!evalMetricId) {
        skip(
          "No eval metric found on selected experiment for eval-stat coverage.",
        );
      }
      const [evalStats, feedbackTemplate, legacyEvalStats] = await Promise.all([
        client.get(
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/evaluations/{evaluation_id}/stats/",
            { experiment_id: experimentId, evaluation_id: evalMetricId },
          ),
        ),
        client.get(
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/",
            { experiment_id: experimentId },
          ),
          { query: { user_eval_metric_id: evalMetricId } },
        ),
        client.get(
          apiPath(
            "/model-hub/experiments/{experiment_id}/evaluations/{evaluation_id}/stats/",
            { experiment_id: experimentId, evaluation_id: evalMetricId },
          ),
        ),
      ]);
      assertExperimentEvalStatsPayload(evalStats, experimentId, evalMetricId);
      assertExperimentFeedbackTemplatePayload(feedbackTemplate);
      assertExperimentEvalStatsPayload(
        legacyEvalStats,
        experimentId,
        evalMetricId,
      );

      evidence.push({
        experiment_id: experimentId,
        experiment_name: detail.name,
        v2_list_rows: listRows.length,
        legacy_list_rows: legacyList.length,
        snapshot_rows: Number(dbAudit.snapshot_row_count),
        rows_returned: tableRows.length,
        column_config: rowsPayload.column_config.length,
        experiment_datasets: Number(dbAudit.fk_experiment_dataset_count),
        prompt_configs: Number(dbAudit.prompt_config_count),
        agent_configs: Number(dbAudit.agent_config_count),
        eval_metrics: Number(dbAudit.eval_metric_count),
        eval_stat_columns: evalStats.evaluation_columns.length,
      });
    },
  },
  {
    id: "EXP-API-002",
    title: "Experiment V2 feedback create, submit, details, and cleanup",
    tags: ["experiments", "feedback", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      apiBase,
      tokens,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const { row: experimentRow, detail } =
        await selectExperimentForReadCoverage(client);
      const experimentId = experimentRow.id;
      const candidate = await loadExperimentFeedbackCandidate(
        experimentId,
        organizationId,
        workspaceId,
      );
      if (
        !candidate?.user_eval_metric_id ||
        !candidate?.eval_column_id ||
        !candidate?.row_id
      ) {
        skip(
          "No experiment feedback candidate found for V2 feedback coverage.",
        );
      }
      assertExperimentFeedbackCandidate(candidate, {
        experimentId,
        organizationId,
        workspaceId,
      });

      const template = await client.get(
        apiPath(
          "/model-hub/experiments/v2/{experiment_id}/feedback/get-template/",
          { experiment_id: experimentId },
        ),
        { query: { user_eval_metric_id: candidate.user_eval_metric_id } },
      );
      assertExperimentFeedbackTemplatePayload(template);

      const explanation = `api journey experiment feedback ${runId}`;
      const created = await client.post(
        apiPath("/model-hub/experiments/v2/{experiment_id}/feedback/", {
          experiment_id: experimentId,
        }),
        {
          source: "experiment",
          source_id: candidate.eval_column_id,
          user_eval_metric: candidate.user_eval_metric_id,
          value: candidate.feedback_value,
          explanation,
          row_id: candidate.row_id,
        },
      );
      assert(
        isUuid(created?.id),
        "Experiment feedback create did not return id.",
      );
      const feedbackId = created.id;
      cleanup.defer("delete experiment feedback", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
          ),
        ),
      );

      const activeAudit = await loadExperimentFeedbackDbAudit({
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
      });
      assertExperimentFeedbackDbAudit(activeAudit, {
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedActionType: null,
      });

      const details = await client.get(
        apiPath(
          "/model-hub/experiments/v2/{experiment_id}/feedback/get-feedback-details/",
          { experiment_id: experimentId },
        ),
        {
          query: {
            user_eval_metric_id: candidate.user_eval_metric_id,
            row_id: candidate.row_id,
          },
        },
      );
      assert(
        payloadArray(details?.feedback, "feedback").some(
          (feedback) =>
            feedback.id === feedbackId && feedback.comment === explanation,
        ),
        "Experiment feedback details did not include the created feedback.",
      );

      const submitted = await client.post(
        apiPath(
          "/model-hub/experiments/v2/{experiment_id}/feedback/submit-feedback/",
          { experiment_id: experimentId },
        ),
        {
          feedback_id: feedbackId,
          user_eval_metric_id: candidate.user_eval_metric_id,
          action_type: "retune",
          value: candidate.feedback_value,
          explanation: `${explanation} retuned`,
        },
      );
      assert(
        submitted?.action_type === "retune",
        "Experiment feedback submit did not persist retune action.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/experiments/v2/{experiment_id}/feedback/", {
              experiment_id: experimentId,
            }),
            {
              source: "experiment",
              source_id: randomUUID(),
              user_eval_metric: candidate.user_eval_metric_id,
              value: candidate.feedback_value,
              explanation: "bad source guard",
              row_id: candidate.row_id,
            },
          ),
        400,
        "Experiment feedback accepted a source column outside the snapshot.",
      );

      const submittedAudit = await loadExperimentFeedbackDbAudit({
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
      });
      assertExperimentFeedbackDbAudit(submittedAudit, {
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedActionType: "retune",
      });

      await client.delete(
        apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
      );
      const deletedAudit = await loadExperimentFeedbackDbAudit({
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
      });
      assertExperimentFeedbackDbAudit(deletedAudit, {
        feedbackId,
        experimentId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedActionType: "retune",
      });

      evidence.push({
        experiment_id: experimentId,
        experiment_name: detail.name,
        feedback_id: feedbackId,
        row_id: candidate.row_id,
        eval_column_id: candidate.eval_column_id,
        user_eval_metric_id: candidate.user_eval_metric_id,
        action_type: submittedAudit.action_type,
        feedback_deleted_at_set: deletedAudit.feedback_deleted_at_set,
      });
    },
  },
  {
    id: "EXP-API-003",
    title: "Experiment action guards, row diff scope, and compare execution",
    tags: ["experiments", "mutating", "guard", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      apiBase,
      tokens,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const { row: experimentRow, detail } =
        await selectExperimentForReadCoverage(client);
      const experimentId = experimentRow.id;
      const datasetId = detail.dataset_id || experimentRow.dataset;
      const candidate = await loadExperimentActionGuardCandidate(
        experimentId,
        organizationId,
        workspaceId,
      );
      if (
        !candidate?.snapshot_row_id ||
        !candidate?.snapshot_column_id ||
        !candidate?.snapshot_dataset_id
      ) {
        skip(
          "Selected experiment lacks a snapshot row/column for action guards.",
        );
      }

      const outside = await createExperimentGuardOutsideDataset(
        client,
        cleanup,
        runId,
        evidence,
      );

      const rowDiff = await client.post(
        apiPath("/model-hub/experiments/v2/row-diff/"),
        {
          experiment_id: experimentId,
          column_ids: [candidate.snapshot_column_id],
          row_ids: [candidate.snapshot_row_id],
          compare_column_ids: [candidate.snapshot_column_id],
        },
      );
      assert(
        rowDiff?.[candidate.snapshot_row_id]?.[candidate.snapshot_column_id],
        "Experiment V2 row-diff did not return the requested snapshot cell.",
      );

      const rowDiffScopeError = await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/experiments/v2/row-diff/"), {
            experiment_id: experimentId,
            column_ids: [outside.column_id],
            row_ids: [outside.row_id],
            compare_column_ids: [outside.column_id],
          }),
        400,
        "Experiment row-diff accepted a row/column outside the snapshot.",
      );
      assert(
        !JSON.stringify(rowDiffScopeError.body || {}).includes(
          outside.cell_value,
        ),
        "Experiment row-diff error leaked the outside dataset cell value.",
      );

      const beforeRerunColumn = await loadColumnStatus(
        candidate.snapshot_column_id,
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/experiments/v2/{experiment_id}/rerun-cells/", {
              experiment_id: experimentId,
            }),
            {
              cells: [
                {
                  column_id: candidate.snapshot_column_id,
                  row_id: outside.row_id,
                },
              ],
              max_concurrent_rows: 1,
            },
          ),
        400,
        "Experiment rerun-cells accepted a row outside the snapshot.",
      );
      const afterRerunColumn = await loadColumnStatus(
        candidate.snapshot_column_id,
      );
      assert(
        afterRerunColumn.status === beforeRerunColumn.status,
        "Rejected rerun-cells changed the snapshot column status.",
      );

      const randomExperimentId = randomUUID();
      await Promise.all([
        expectApiErrorStatus(
          () =>
            client.post(apiPath("/model-hub/experiments/re-run/"), {
              experiment_ids: [randomExperimentId],
            }),
          404,
          "Legacy experiment re-run accepted an unknown experiment id.",
        ),
        expectApiErrorStatus(
          () =>
            client.post(apiPath("/model-hub/experiments/v2/re-run/"), {
              experiment_ids: [randomExperimentId],
            }),
          404,
          "Experiment V2 re-run accepted an unknown experiment id.",
        ),
        expectApiErrorStatus(
          () =>
            client.delete(apiPath("/model-hub/experiments/delete/"), {
              body: { experiment_ids: [randomExperimentId] },
            }),
          404,
          "Legacy experiment delete accepted an unknown experiment id.",
        ),
        expectApiErrorStatus(
          () =>
            client.delete(apiPath("/model-hub/experiments/v2/delete/"), {
              body: { experiment_ids: [randomExperimentId] },
            }),
          404,
          "Experiment V2 delete accepted an unknown experiment id.",
        ),
        expectApiErrorStatus(
          () =>
            client.post(
              apiPath(
                "/model-hub/experiments/{experiment_id}/run-evaluations/",
                { experiment_id: experimentId },
              ),
              { eval_template_ids: [randomUUID()] },
            ),
          400,
          "Experiment run-evaluations accepted an unknown eval metric id.",
        ),
      ]);

      let stopGuard = "skipped-running";
      if (
        !["running", "queued"].includes(String(candidate.status).toLowerCase())
      ) {
        await expectApiErrorStatus(
          () =>
            client.post(
              apiPath("/model-hub/experiments/v2/{experiment_id}/stop/", {
                experiment_id: experimentId,
              }),
              {},
            ),
          400,
          "Experiment stop accepted a non-running experiment.",
        );
        stopGuard = "non-running-rejected";
      }

      const [v2Compare, legacyCompare] = await Promise.all([
        client.post(
          apiPath(
            "/model-hub/experiments/v2/{experiment_id}/compare-experiments/",
            { experiment_id: experimentId },
          ),
          { weights: {} },
        ),
        client.post(
          apiPath(
            "/model-hub/experiments/{experiment_id}/compare-experiments/",
            {
              experiment_id: experimentId,
            },
          ),
          { weights: {} },
        ),
      ]);
      assertExperimentComparePostPayload(v2Compare, experimentId, "V2");
      assertExperimentComparePostPayload(legacyCompare, experimentId, "legacy");

      const afterAudit = await loadExperimentReadDbAudit(
        experimentId,
        organizationId,
        workspaceId,
      );
      assert(
        afterAudit.status === candidate.status,
        "Guarded experiment actions changed the selected experiment status.",
      );

      evidence.push({
        experiment_id: experimentId,
        dataset_id: datasetId,
        snapshot_dataset_id: candidate.snapshot_dataset_id,
        snapshot_row_id: candidate.snapshot_row_id,
        snapshot_column_id: candidate.snapshot_column_id,
        outside_dataset_id: outside.dataset_id,
        outside_row_id: outside.row_id,
        outside_column_id: outside.column_id,
        stop_guard: stopGuard,
        v2_compare_datasets: Number(v2Compare.total_datasets),
        legacy_compare_datasets: Number(legacyCompare.total_datasets),
        comparison_count_after: Number(afterAudit.comparison_count),
      });
    },
  },
  {
    id: "EXP-API-004",
    title: "Legacy experiment collection create, update, and scope guards",
    tags: ["experiments", "legacy", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const fixture = await seedLegacyExperimentCreateUpdateFixture({
        runId,
        organizationId,
        workspaceId,
        userId: currentUserId(user),
      });
      assert(
        fixture?.fixture_created,
        "Legacy experiment create/update fixture seed failed.",
      );
      cleanup.defer("hard delete legacy experiment create/update fixture", () =>
        hardDeleteLegacyExperimentCreateUpdateFixture(fixture),
      );

      const createName = `api journey legacy create ${runId}`;
      const createPromptConfig = {
        model: "gpt-4o-mini",
        temperature: 0.1,
        journey: "EXP-API-004-create",
      };
      const created = await client.post(apiPath("/model-hub/experiments/"), {
        name: createName,
        dataset_id: fixture.dataset_id,
        column_id: fixture.output_column_id,
        prompt_config: createPromptConfig,
        user_eval_template_ids: [fixture.eval_metric_id],
      });
      assert(
        String(created).includes("Experiment created successfully"),
        "Legacy experiment create returned an unexpected response.",
      );

      const createdAudit = await loadLegacyExperimentCreateUpdateAudit({
        datasetId: fixture.dataset_id,
        experimentName: createName,
        organizationId,
        workspaceId,
      });
      fixture.created_experiment_id = createdAudit.experiment_id;
      assertLegacyExperimentAudit(createdAudit, {
        experimentName: createName,
        datasetId: fixture.dataset_id,
        columnId: fixture.output_column_id,
        organizationId,
        workspaceId,
        metricIds: [fixture.eval_metric_id],
        promptConfigJourney: "EXP-API-004-create",
      });

      const legacyDetail = await client.get(
        apiPath("/model-hub/experiments/"),
        {
          query: { experiment_id: createdAudit.experiment_id },
        },
      );
      assert(
        legacyDetail?.name === createName &&
          legacyDetail?.dataset_id === fixture.dataset_id,
        "Legacy experiment detail did not read back the created experiment.",
      );

      const updateName = `api journey legacy update ${runId}`;
      const updatePromptConfig = {
        model: "gpt-4o-mini",
        temperature: 0.2,
        journey: "EXP-API-004-update",
      };
      const updated = await client.put(apiPath("/model-hub/experiments/"), {
        experiment_id: createdAudit.experiment_id,
        re_run: false,
        name: updateName,
        dataset_id: fixture.dataset_id,
        column_id: fixture.output_column_id,
        prompt_config: updatePromptConfig,
        user_eval_template_ids: [fixture.second_eval_metric_id],
      });
      assert(
        String(updated).includes("Experiment updated successfully"),
        "Legacy experiment update returned an unexpected response.",
      );

      const updatedAudit = await loadLegacyExperimentCreateUpdateAudit({
        experimentId: createdAudit.experiment_id,
        datasetId: fixture.dataset_id,
        organizationId,
        workspaceId,
      });
      assertLegacyExperimentAudit(updatedAudit, {
        experimentName: updateName,
        datasetId: fixture.dataset_id,
        columnId: fixture.output_column_id,
        organizationId,
        workspaceId,
        metricIds: [fixture.second_eval_metric_id],
        promptConfigJourney: "EXP-API-004-update",
      });

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/experiments/"), {
            name: fixture.blocked_column_experiment_name,
            dataset_id: fixture.dataset_id,
            column_id: fixture.blocked_column_id,
            prompt_config: createPromptConfig,
          }),
        404,
        "Legacy experiment create accepted a column from another dataset.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/experiments/"), {
            name: fixture.blocked_metric_experiment_name,
            dataset_id: fixture.dataset_id,
            column_id: fixture.output_column_id,
            prompt_config: createPromptConfig,
            user_eval_template_ids: [fixture.blocked_eval_metric_id],
          }),
        404,
        "Legacy experiment create accepted an eval metric from another dataset.",
      );

      let otherWorkspaceCreateStatus = "skipped:no-other-workspace";
      let otherWorkspaceDetailGuard = "skipped:no-other-workspace";
      let otherWorkspaceUpdateGuard = "skipped:no-other-workspace";
      if (fixture.other_workspace_id) {
        try {
          await client.post(apiPath("/model-hub/experiments/"), {
            name: fixture.other_workspace_create_name,
            dataset_id: fixture.other_dataset_id,
            column_id: fixture.other_column_id,
            prompt_config: createPromptConfig,
          });
          throw new Error(
            "Legacy experiment create accepted another workspace dataset.",
          );
        } catch (error) {
          assert(
            [400, 404].includes(error?.status),
            `Other-workspace legacy create returned HTTP ${error?.status}.`,
          );
          otherWorkspaceCreateStatus = error.status;
        }

        await expectApiErrorStatus(
          () =>
            client.get(apiPath("/model-hub/experiments/"), {
              query: { experiment_id: fixture.other_experiment_id },
            }),
          404,
          "Legacy experiment detail exposed another workspace experiment.",
        );
        otherWorkspaceDetailGuard = "passed";

        await expectApiErrorStatus(
          () =>
            client.put(apiPath("/model-hub/experiments/"), {
              experiment_id: fixture.other_experiment_id,
              re_run: false,
              name: "api journey other workspace update blocked",
              dataset_id: fixture.dataset_id,
              column_id: fixture.output_column_id,
              prompt_config: updatePromptConfig,
            }),
          404,
          "Legacy experiment update accepted another workspace experiment.",
        );
        otherWorkspaceUpdateGuard = "passed";
      }

      const guardAudit = await loadLegacyExperimentGuardAudit(fixture);
      assert(
        Number(guardAudit.blocked_column_count) === 0,
        "Blocked column legacy create persisted an experiment.",
      );
      assert(
        Number(guardAudit.blocked_metric_count) === 0,
        "Blocked metric legacy create persisted an experiment.",
      );
      assert(
        Number(guardAudit.other_workspace_create_count) === 0,
        "Blocked other-workspace legacy create persisted an experiment.",
      );
      if (fixture.other_experiment_id) {
        assert(
          guardAudit.other_experiment_name === fixture.other_experiment_name,
          "Blocked other-workspace legacy update mutated the experiment name.",
        );
      }

      evidence.push({
        experiment_id: createdAudit.experiment_id,
        dataset_id: fixture.dataset_id,
        output_column_id: fixture.output_column_id,
        created_name: createName,
        updated_name: updatedAudit.experiment_name,
        create_metric_id: fixture.eval_metric_id,
        update_metric_id: fixture.second_eval_metric_id,
        blocked_column_guard: "passed",
        blocked_metric_guard: "passed",
        other_workspace_create_status: otherWorkspaceCreateStatus,
        other_workspace_detail_guard: otherWorkspaceDetailGuard,
        other_workspace_update_guard: otherWorkspaceUpdateGuard,
      });
    },
  },
  {
    id: "DPE-API-002",
    title:
      "Dataset create, column add, row add, cell edit, download, and delete lifecycle",
    tags: ["dataset", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const datasetName = `api journey dataset ${runId}`;
      const columnName = `api_journey_text_${runId.replace(/[^a-z0-9]/gi, "_")}`;
      const cellValue = `dataset cell ${runId}`;

      const dataset = await createOrResolveWritableDataset(
        client,
        cleanup,
        datasetName,
        evidence,
      );
      const datasetId = dataset.id;

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
          dataset_id: datasetId,
        }),
        {
          new_column_name: columnName,
          column_type: "text",
          source: "OTHERS",
        },
      );

      let table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const column = findColumn(table, columnName);
      assert(column?.id, "Added dataset column was not visible after reload.");

      table = await getDatasetTable(client, datasetId);
      let row = firstDatasetRow(table);
      let addedRow = false;
      if (!row?.row_id) {
        await client.post(
          apiPath("/model-hub/develops/{dataset_id}/add_empty_rows/", {
            dataset_id: datasetId,
          }),
          { num_rows: 1 },
        );
        addedRow = true;
        table = await getDatasetTable(client, datasetId);
        row = firstDatasetRow(table);
      }
      assert(row?.row_id, "Added dataset row was not visible after reload.");

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
          dataset_id: datasetId,
        }),
        {
          row_id: row.row_id,
          column_id: column.id,
          new_value: cellValue,
        },
      );

      const reloaded = await getDatasetTable(client, datasetId);
      const reloadedRow = asArray(reloaded.table).find(
        (candidate) => candidate.row_id === row.row_id,
      );
      assert(
        cellValueFor(reloadedRow, column.id) === cellValue,
        "Updated dataset cell value did not round-trip through get-dataset-table.",
      );

      const downloaded = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/download_dataset/", {
          dataset_id: datasetId,
        }),
      );
      assert(
        String(downloaded).includes(cellValue),
        "Downloaded dataset did not include the edited cell value.",
      );

      if (addedRow) {
        await client.delete(
          apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
            dataset_id: datasetId,
          }),
          {
            body: { row_ids: [row.row_id], selected_all_rows: false },
          },
        );
        const afterRowDelete = await getDatasetTable(client, datasetId);
        assert(
          !asArray(afterRowDelete.table).some(
            (candidate) => candidate.row_id === row.row_id,
          ),
          "Deleted dataset row was still visible after reload.",
        );
      }

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
          dataset_id: datasetId,
          column_id: column.id,
        }),
      );
      const afterColumnDelete = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      assert(
        !findColumn(afterColumnDelete, columnName),
        "Deleted dataset column was still visible after reload.",
      );

      evidence.push({
        dataset_id: datasetId,
        dataset_source: dataset.source,
        column_id: column.id,
        row_id: row.row_id,
        added_row: addedRow,
      });
    },
  },
  {
    id: "DPE-API-016",
    title: "Dataset advanced row/column readbacks and cleanup audit",
    tags: ["dataset", "develop", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      apiBase,
      tokens,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const datasetName = `api journey dataset advanced ${runId}`;
      const multiTextName = `api_adv_text_${suffix}`;
      const renamedTextName = `api_adv_text_renamed_${suffix}`;
      const multiBoolName = `api_adv_bool_${suffix}`;
      const addIntegerName = `api_adv_int_${suffix}`;
      const addJsonName = `api_adv_json_${suffix}`;
      const rowId = randomUUID();
      const textValue = `advanced row ${runId}`;
      const jsonValue = JSON.stringify({ source: "api-journey", runId });
      const tempColumnIds = [];
      const deletedColumnIds = new Set();
      let rowDeleted = false;

      const dataset = await createOrResolveWritableDataset(
        client,
        cleanup,
        datasetName,
        evidence,
      );
      const datasetId = dataset.id;
      cleanup.defer("delete advanced dataset row", async () => {
        if (rowDeleted) return;
        await client.delete(
          apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
            dataset_id: datasetId,
          }),
          { body: { row_ids: [rowId], selected_all_rows: false } },
        );
      });
      cleanup.defer("delete advanced dataset columns", async () => {
        for (const columnId of tempColumnIds) {
          if (deletedColumnIds.has(columnId)) continue;
          await ignoreNotFound(() =>
            client.delete(
              apiPath(
                "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
                { dataset_id: datasetId, column_id: columnId },
              ),
            ),
          );
        }
      });

      await client.post(
        apiPath(
          "/model-hub/develops/{dataset_id}/add_multiple_static_columns/",
          {
            dataset_id: datasetId,
          },
        ),
        {
          columns: [
            {
              new_column_name: multiTextName,
              column_type: "text",
              source: "OTHERS",
            },
            {
              new_column_name: multiBoolName,
              column_type: "boolean",
              source: "OTHERS",
            },
          ],
        },
      );

      const typedColumns = await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_columns/", {
          dataset_id: datasetId,
        }),
        {
          new_columns_data: [
            { name: addIntegerName, data_type: "integer", source: "OTHERS" },
            { name: addJsonName, data_type: "json", source: "OTHERS" },
          ],
        },
      );
      const typedColumnRows = payloadArray(typedColumns?.data, "data");
      assert(
        typedColumnRows.length === 2,
        "add_columns did not return the two created columns.",
      );

      const emptyColumns = await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_empty_columns/", {
          dataset_id: datasetId,
        }),
        { num_cols: 1 },
      );
      const emptyColumnRows = payloadArray(emptyColumns?.data, "data");
      assert(
        emptyColumnRows.length === 1 && isUuid(emptyColumnRows[0].id),
        "add_empty_columns did not return the created column id.",
      );

      let table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const multiTextColumn = findColumn(table, multiTextName);
      const multiBoolColumn = findColumn(table, multiBoolName);
      const addIntegerColumn = findColumn(table, addIntegerName);
      const addJsonColumn = findColumn(table, addJsonName);
      const emptyColumn = asArray(table?.column_config).find(
        (column) => column.id === emptyColumnRows[0].id,
      );
      for (const column of [
        multiTextColumn,
        multiBoolColumn,
        addIntegerColumn,
        addJsonColumn,
        emptyColumn,
      ]) {
        assert(column?.id, "A newly created dataset column was not visible.");
        tempColumnIds.push(column.id);
      }

      await client.put(
        apiPath(
          "/model-hub/develops/{dataset_id}/update_column_name/{column_id}/",
          { dataset_id: datasetId, column_id: multiTextColumn.id },
        ),
        { new_column_name: renamedTextName },
      );

      const conversionPreview = await client.put(
        apiPath(
          "/model-hub/develops/{dataset_id}/update_column_type/{column_id}/",
          { dataset_id: datasetId, column_id: addIntegerColumn.id },
        ),
        { new_column_type: "text", preview: true, force_update: false },
      );
      assert(
        conversionPreview?.new_data_type === "text",
        "Column type preview did not return requested data type.",
      );
      assert(
        Number.isInteger(Number(conversionPreview.invalid_count)),
        "Column type preview did not expose invalid_count.",
      );

      table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const renamedTextColumn = findColumn(table, renamedTextName);
      assert(
        renamedTextColumn?.id === multiTextColumn.id,
        "Renamed dataset column was not visible after reload.",
      );

      await client.put(
        apiPath("/model-hub/develops/{dataset_id}/edit_dataset_behavior/", {
          dataset_id: datasetId,
        }),
        {
          column_config: {
            [renamedTextColumn.id]: { is_visible: true, is_frozen: "left" },
            [multiBoolColumn.id]: { is_visible: false, is_frozen: null },
          },
          dataset_config: { dismiss_banner: true },
        },
      );

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_rows/", {
          dataset_id: datasetId,
        }),
        {
          rows: [
            {
              id: rowId,
              cells: [
                { column_name: renamedTextName, value: textValue },
                { column_name: multiBoolName, value: "true" },
                { column_name: addIntegerName, value: "41" },
                { column_name: addJsonName, value: jsonValue },
              ],
            },
          ],
        },
      );

      await Promise.all([
        client.post(
          apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
            dataset_id: datasetId,
          }),
          {
            row_id: rowId,
            column_id: addIntegerColumn.id,
            new_value: 42,
          },
        ),
        client.post(
          apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
            dataset_id: datasetId,
          }),
          {
            row_id: rowId,
            column_id: addJsonColumn.id,
            new_value: jsonValue,
          },
        ),
      ]);

      const [rowData, cellData, reloadedTable] = await Promise.all([
        client.post(
          apiPath("/model-hub/develops/{dataset_id}/get-row-data/", {
            dataset_id: datasetId,
          }),
          { row_id: rowId, filters: [], sort: [] },
        ),
        client.post(apiPath("/model-hub/develops/get-cell-data/"), {
          row_ids: [rowId],
          column_ids: [
            renamedTextColumn.id,
            multiBoolColumn.id,
            addIntegerColumn.id,
            addJsonColumn.id,
          ],
        }),
        getDatasetTable(client, datasetId, { page_size: 100 }),
      ]);
      assertAdvancedDatasetReadbacks(rowData, cellData, reloadedTable, {
        rowId,
        renamedTextColumn,
        multiBoolColumn,
        addIntegerColumn,
        addJsonColumn,
        textValue,
        jsonValue,
      });

      const activeAudit = await loadDatasetRowColumnLifecycleDbAudit(
        datasetId,
        [rowId],
        tempColumnIds,
        organizationId,
        workspaceId,
      );
      assertDatasetRowColumnLifecycleDbAudit(activeAudit, {
        datasetId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        rowCount: 1,
        columnCount: tempColumnIds.length,
      });

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
          dataset_id: datasetId,
        }),
        { body: { row_ids: [rowId], selected_all_rows: false } },
      );
      rowDeleted = true;

      for (const columnId of tempColumnIds) {
        await client.delete(
          apiPath(
            "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
            {
              dataset_id: datasetId,
              column_id: columnId,
            },
          ),
        );
        deletedColumnIds.add(columnId);
      }

      const deletedAudit = await loadDatasetRowColumnLifecycleDbAudit(
        datasetId,
        [rowId],
        tempColumnIds,
        organizationId,
        workspaceId,
      );
      assertDatasetRowColumnLifecycleDbAudit(deletedAudit, {
        datasetId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        rowCount: 1,
        columnCount: tempColumnIds.length,
      });

      evidence.push({
        dataset_id: datasetId,
        dataset_source: dataset.source,
        row_id: rowId,
        temp_columns: tempColumnIds.length,
        deleted_columns_with_deleted_at:
          deletedAudit.deleted_column_deleted_at_count,
        deleted_rows_with_deleted_at: deletedAudit.deleted_row_deleted_at_count,
        deleted_cells_with_deleted_at:
          deletedAudit.deleted_cell_deleted_at_count,
        stale_column_config_entries:
          deletedAudit.column_config_contains_deleted_count,
      });
    },
  },
  {
    id: "DPE-API-017",
    title: "Dataset SDK snippets use placeholder credentials",
    tags: ["dataset", "develop", "mutating", "credential-safety", "db-audit"],
    async run({ client, evidence, user, organizationId }) {
      requireMutations();
      const email = currentUserEmail(user);
      if (!email) skip("Current user email was not available for DB audit.");

      const datasets = asArray(
        await client.get(apiPath("/model-hub/develops/get-datasets/"), {
          query: { page: 0, page_size: 10 },
        }),
      );
      const dataset = datasets.find((row) => row?.id);
      if (!dataset) skip("No datasets found for SDK snippet coverage.");

      const beforeAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );
      const result = await client.post(
        apiPath("/model-hub/develops/add_rows_sdk/"),
        { dataset_id: dataset.id },
      );
      const afterAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );

      assert(
        result?.dataset?.id === dataset.id,
        "add_rows_sdk returned a different dataset.",
      );
      assertDatasetSdkSnippetSafety(result);
      assert(
        Number(afterAudit.user_key_count) ===
          Number(beforeAudit.user_key_count),
        "add_rows_sdk created or deleted a user API key while generating snippets.",
      );

      evidence.push({
        dataset_id: dataset.id,
        code_keys: Object.keys(result.code || {}).sort(),
        user_key_count_before: Number(beforeAudit.user_key_count),
        user_key_count_after: Number(afterAudit.user_key_count),
        placeholders_present: true,
      });
    },
  },
  {
    id: "DPE-API-018",
    title: "Legacy dataset annotations lifecycle, guards, and cleanup",
    tags: ["dataset", "annotations", "legacy", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      user,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const userId = currentUserId(user);
      if (!userId) skip("Current user id was not available for annotations.");

      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const datasetName = `api journey legacy annotation ${runId}`;
      const staticColumnName = `api_legacy_annotation_text_${suffix}`;
      const staticCellValue = `legacy annotation static ${runId}`;
      const labelName = `api journey legacy annotation ${runId}`;
      const detailAnnotationName = `api journey legacy annotation detail ${runId}`;
      const bulkAnnotationName = `api journey legacy annotation bulk ${runId}`;
      const patchedAnnotationName = `${detailAnnotationName} patched`;
      const updatedAnnotationName = `${detailAnnotationName} updated`;
      const createdAnnotationIds = [];
      const deletedAnnotationIds = new Set();
      let staticColumnId = null;
      let rowId = null;
      let rowOrder = 0;
      let addedRow = false;
      let labelId = null;
      let annotationTaskListCount = 0;
      let annotationTaskReadStatus = "skipped_no_task";
      let annotationTaskFixture = null;
      let annotationTaskCleanup = null;
      let annotationTaskFixtureCleaned = false;

      const dataset = await createOrResolveWritableDataset(
        client,
        cleanup,
        datasetName,
        evidence,
      );
      const datasetId = dataset.id;

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
          dataset_id: datasetId,
        }),
        {
          new_column_name: staticColumnName,
          column_type: "text",
          source: "OTHERS",
        },
      );
      let table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const staticColumn = findColumn(table, staticColumnName);
      assert(
        staticColumn?.id,
        "Temporary static annotation column was not visible.",
      );
      staticColumnId = staticColumn.id;
      cleanup.defer("delete legacy annotation static column", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
              {
                dataset_id: datasetId,
                column_id: staticColumnId,
              },
            ),
          ),
        ),
      );

      table = await getDatasetTable(client, datasetId);
      let row = firstDatasetRow(table);
      if (!row?.row_id) {
        await client.post(
          apiPath("/model-hub/develops/{dataset_id}/add_empty_rows/", {
            dataset_id: datasetId,
          }),
          { num_rows: 1 },
        );
        addedRow = true;
        table = await getDatasetTable(client, datasetId);
        row = firstDatasetRow(table);
      }
      assert(
        row?.row_id,
        "No dataset row was available for annotation coverage.",
      );
      rowId = row.row_id;
      rowOrder = Number(row.row_order ?? row.order ?? row.row_number - 1 ?? 0);
      if (addedRow) {
        cleanup.defer("delete legacy annotation row", () =>
          ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
                dataset_id: datasetId,
              }),
              {
                body: { row_ids: [rowId], selected_all_rows: false },
              },
            ),
          ),
        );
      }

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
          dataset_id: datasetId,
        }),
        {
          row_id: rowId,
          column_id: staticColumnId,
          new_value: staticCellValue,
        },
      );

      const createdLabel = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "numeric",
          description: "Temporary label for legacy annotations API journey.",
          settings: {
            min: 0,
            max: 10,
            step_size: 1,
            display_type: "slider",
          },
        },
      );
      labelId = createdLabel?.id
        ? createdLabel.id
        : (await findAnnotationLabelByName(client, labelName))?.id;
      assert(labelId, "Legacy annotation label create did not return id.");
      cleanup.defer("delete legacy annotation label", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
          ),
        ),
      );

      annotationTaskFixture = await seedAnnotationTaskFixture({
        runId,
        userId,
        organizationId,
        workspaceId,
      });
      assert(
        annotationTaskFixture?.fixture_created === true,
        "AnnotationTask fixture seed did not create a task.",
      );
      cleanup.defer("hard delete API journey annotation task fixture", async () => {
        if (!annotationTaskFixtureCleaned) {
          await hardDeleteAnnotationTaskFixture(annotationTaskFixture);
        }
      });

      const annotationTasks = asArray(
        await client.get(apiPath("/model-hub/annotation-tasks/"), {
          query: {
            page: 1,
            limit: 10,
            predictive_journey: annotationTaskFixture.ai_model_id,
          },
        }),
      );
      annotationTaskListCount = annotationTasks.length;
      const annotationTask = annotationTasks.find(
        (task) => task?.id === annotationTaskFixture.annotation_task_id,
      );
      assert(
        annotationTask?.id,
        "Seeded AnnotationTask was not returned by the filtered list.",
      );
      assertAnnotationTaskPayload(annotationTask, annotationTaskFixture, userId);
      const annotationTaskDetail = await client.get(
        apiPath("/model-hub/annotation-tasks/{id}/", {
          id: annotationTask.id,
        }),
      );
      assertAnnotationTaskPayload(
        annotationTaskDetail,
        annotationTaskFixture,
        userId,
      );
      annotationTaskReadStatus = 200;
      annotationTaskCleanup =
        await hardDeleteAnnotationTaskFixture(annotationTaskFixture);
      annotationTaskFixtureCleaned = true;
      assert(
        Number(annotationTaskCleanup.remaining_task_count) === 0,
        "AnnotationTask fixture cleanup left a task row.",
      );
      assert(
        Number(annotationTaskCleanup.remaining_ai_model_count) === 0,
        "AnnotationTask fixture cleanup left an AIModel row.",
      );

      const preview = await client.post(
        apiPath("/model-hub/annotations/preview_annotations/"),
        {
          dataset_id: datasetId,
          static_column: [staticColumnId],
        },
      );
      const previewData = preview?.preview_data || preview?.data?.preview_data;
      if (preview?.row_id) {
        rowId = preview.row_id;
        rowOrder = Number(preview.row_number ?? rowOrder);
        await client.post(
          apiPath("/model-hub/develops/{dataset_id}/update_cell_value/", {
            dataset_id: datasetId,
          }),
          {
            row_id: rowId,
            column_id: staticColumnId,
            new_value: staticCellValue,
          },
        );
      }
      assert(
        asArray(previewData?.static_fields).some(
          (field) => field.column_id === staticColumnId,
        ),
        "Legacy annotation preview did not return the temporary static field.",
      );

      await client.post(apiPath("/model-hub/annotations/"), {
        name: detailAnnotationName,
        dataset: datasetId,
        assigned_users: [userId],
        labels: [{ id: labelId, required: false }],
        responses: 1,
        static_fields: [
          {
            column_id: staticColumnId,
            type: "plain_text",
            view: "default_open",
          },
        ],
      });
      const detailAnnotation = await findLegacyAnnotationByName(
        client,
        datasetId,
        detailAnnotationName,
      );
      assert(detailAnnotation?.id, "Created legacy annotation was not listed.");
      createdAnnotationIds.push(detailAnnotation.id);
      cleanup.defer("delete legacy detail annotation", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations/{id}/", {
              id: detailAnnotation.id,
            }),
          ),
        ),
      );

      const listedAnnotations = asArray(
        await client.get(apiPath("/model-hub/annotations/"), {
          query: { dataset: datasetId },
        }),
      );
      assert(
        listedAnnotations.some(
          (annotation) => annotation.id === detailAnnotation.id,
        ),
        "Dataset-filtered annotations list did not include the created annotation.",
      );

      const annotateRow = await client.get(
        apiPath("/model-hub/annotations/{id}/annotate_row/", {
          id: detailAnnotation.id,
        }),
        { query: { row_order: rowOrder } },
      );
      const annotateData = annotateRow?.data || annotateRow;
      const labelCell = asArray(annotateData?.label).find(
        (label) => label.label_id === labelId,
      );
      assert(
        labelCell?.column_id,
        "annotate_row did not expose label column id.",
      );
      assert(
        asArray(annotateData?.static_fields).some(
          (field) => field.column_id === staticColumnId,
        ),
        "annotate_row did not include the temporary static field.",
      );

      await client.post(
        apiPath("/model-hub/annotations/{id}/update_cells/", {
          id: detailAnnotation.id,
        }),
        {
          label_values: [
            {
              row_id: rowId,
              label_id: labelId,
              column_id: labelCell.column_id,
              value: 0,
              description: `legacy annotation note ${runId}`,
              time_taken: 0.25,
            },
          ],
        },
      );
      const afterUpdate = await client.get(
        apiPath("/model-hub/annotations/{id}/annotate_row/", {
          id: detailAnnotation.id,
        }),
        { query: { row_order: rowOrder } },
      );
      const updatedLabelCell = asArray(
        (afterUpdate?.data || afterUpdate)?.label,
      ).find((label) => label.label_id === labelId);
      assert(
        Number(updatedLabelCell?.cell_value) === 0,
        "update_cells did not persist numeric zero annotation value.",
      );

      await client.post(
        apiPath("/model-hub/annotations/{id}/reset_annotations/", {
          id: detailAnnotation.id,
        }),
        { row_id: rowId },
      );

      await client.patch(
        apiPath("/model-hub/annotations/{id}/", { id: detailAnnotation.id }),
        { name: patchedAnnotationName },
      );
      const patched = await client.get(
        apiPath("/model-hub/annotations/{id}/", { id: detailAnnotation.id }),
      );
      assert(
        patched.name === patchedAnnotationName,
        "PATCH did not update the legacy annotation name.",
      );
      assert(
        asArray(patched.labels).some((label) => label.id === labelId),
        "PATCH name-only update cleared legacy annotation labels.",
      );
      assert(
        asArray(patched.assigned_users).some(
          (assigned) => assigned.id === userId,
        ),
        "PATCH name-only update cleared legacy annotation assignees.",
      );

      let requiredUpdateStatus = null;
      try {
        await client.put(
          apiPath("/model-hub/annotations/{id}/", { id: detailAnnotation.id }),
          {
            name: `${detailAnnotationName} required`,
            dataset: datasetId,
            assigned_users: [userId],
            labels: [{ id: labelId, required: true }],
            responses: 1,
          },
        );
        requiredUpdateStatus = 200;
      } catch (error) {
        requiredUpdateStatus = error?.status;
        assert(
          requiredUpdateStatus === 402,
          `Required-label update returned HTTP ${requiredUpdateStatus}, expected 402.`,
        );
      }

      await client.put(
        apiPath("/model-hub/annotations/{id}/", { id: detailAnnotation.id }),
        {
          name: updatedAnnotationName,
          dataset: datasetId,
          assigned_users: [userId],
          labels: [{ id: labelId, required: false }],
          responses: 1,
        },
      );

      await client.post(apiPath("/model-hub/annotations/"), {
        name: bulkAnnotationName,
        dataset: datasetId,
        assigned_users: [userId],
        labels: [{ id: labelId, required: false }],
        responses: 1,
      });
      const bulkAnnotation = await findLegacyAnnotationByName(
        client,
        datasetId,
        bulkAnnotationName,
      );
      assert(bulkAnnotation?.id, "Second legacy annotation was not listed.");
      createdAnnotationIds.push(bulkAnnotation.id);
      cleanup.defer("delete legacy bulk annotation", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations/{id}/", {
              id: bulkAnnotation.id,
            }),
          ),
        ),
      );

      const bulkDestroy = await client.post(
        apiPath("/model-hub/annotations/bulk_destroy/"),
        { annotation_ids: [bulkAnnotation.id] },
      );
      const bulkDeletedCount = Number(bulkDestroy?.deleted_count ?? 0);
      assert(
        bulkDeletedCount === 1,
        "bulk_destroy did not delete one annotation.",
      );
      deletedAnnotationIds.add(bulkAnnotation.id);

      await client.delete(
        apiPath("/model-hub/annotations/{id}/", { id: detailAnnotation.id }),
      );
      deletedAnnotationIds.add(detailAnnotation.id);

      const audit = await loadLegacyAnnotationLifecycleDbAudit({
        annotationIds: createdAnnotationIds,
        datasetId,
        labelId,
        organizationId,
        workspaceId,
      });
      assert(
        Number(audit.annotation_count) === createdAnnotationIds.length,
        "Legacy annotation DB audit did not find all created annotations.",
      );
      assert(
        Number(audit.active_annotation_count) === 0,
        "Deleted legacy annotations still had active rows.",
      );
      assert(
        Number(audit.deleted_annotation_with_deleted_at_count) ===
          createdAnnotationIds.length,
        "Deleted legacy annotations were missing deleted_at.",
      );
      assert(
        Number(audit.active_generated_column_count) === 0,
        "Deleted legacy annotations left active generated columns.",
      );
      assert(
        Number(audit.active_generated_cell_count) === 0,
        "Deleted legacy annotations left active generated cells.",
      );

      evidence.push({
        dataset_id: datasetId,
        dataset_source: dataset.source,
        static_column_id: staticColumnId,
        row_id: rowId,
        added_row: addedRow,
        label_id: labelId,
        detail_annotation_id: detailAnnotation.id,
        bulk_annotation_id: bulkAnnotation.id,
        annotation_task_list_count: annotationTaskListCount,
        annotation_task_id: annotationTaskFixture.annotation_task_id,
        annotation_task_ai_model_id: annotationTaskFixture.ai_model_id,
        annotation_task_read_status: annotationTaskReadStatus,
        annotation_task_cleanup: annotationTaskCleanup,
        required_update_status: requiredUpdateStatus,
        bulk_deleted_count: bulkDeletedCount,
        deleted_annotation_ids: Array.from(deletedAnnotationIds),
        generated_column_count: Number(audit.generated_column_count),
        generated_cell_count: Number(audit.generated_cell_count),
        active_annotation_count: Number(audit.active_annotation_count),
        active_generated_column_count: Number(
          audit.active_generated_column_count,
        ),
        active_generated_cell_count: Number(audit.active_generated_cell_count),
      });
    },
  },
  {
    id: "DPE-API-019",
    title: "Dataset compare lifecycle, guards, stats, download, and cleanup",
    tags: ["dataset", "compare", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const candidate = await seedDatasetCompareFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        candidate?.fixture_created,
        "Dataset compare fixture seed did not create datasets.",
      );
      cleanup.defer("hard delete API journey dataset compare fixture", () =>
        hardDeleteDatasetCompareFixture([
          candidate.base_dataset_id,
          candidate.other_dataset_id,
        ]),
      );

      const datasetIds = [candidate.other_dataset_id];
      const fullDatasetIds = [candidate.base_dataset_id, ...datasetIds];
      const compareRequest = {
        page_size: 2,
        current_page_index: 0,
        base_column_name: candidate.base_column_name,
        dataset_ids: datasetIds,
      };
      const comparePath = apiPath(
        "/model-hub/datasets/{dataset_id}/compare-datasets/",
        { dataset_id: candidate.base_dataset_id },
      );
      const compare = await client.post(comparePath, compareRequest);
      assertDatasetComparePayload(compare);
      const compareId = compare?.metadata?.compare_id || compare?.compare_id;
      assert(
        isUuid(compareId),
        "Dataset compare response did not include compare_id.",
      );
      let compareDeleted = false;
      cleanup.defer("delete API journey dataset compare files", async () => {
        if (compareDeleted) return;
        await client.delete(
          apiPath("/model-hub/datasets/delete-compare/{compare_id}/", {
            compare_id: compareId,
          }),
        );
      });

      const firstRow = asArray(compare.table)[0];
      assert(
        isUuid(firstRow?.row_id),
        "Dataset compare response lacked row_id.",
      );
      const rowDetail = await client.get(
        apiPath("/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/", {
          compare_id: compareId,
          row_id: firstRow.row_id,
        }),
      );
      assertDatasetCompareRowPayload(rowDetail, firstRow.row_id);

      let comparePageTwoRows = null;
      if (Number(compare?.metadata?.total_pages || 0) > 1) {
        const comparePageTwo = await client.post(comparePath, {
          ...compareRequest,
          compare_id: compareId,
          current_page_index: 1,
        });
        assert(
          comparePageTwo?.metadata?.compare_id === compareId,
          "Dataset compare pagination did not reuse compare_id.",
        );
        comparePageTwoRows = asArray(comparePageTwo.table).length;
      }

      const evalsPayload = await client.post(
        apiPath("/model-hub/datasets/compare/get-evals-list/"),
        {
          eval_type: "user",
          dataset_ids: fullDatasetIds,
          search_text: "",
        },
      );
      const evals = payloadArray(evalsPayload, "evals");
      assert(
        Array.isArray(evals),
        "Compare evals-list did not return evals array.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/datasets/compare/get-evals-list/"), {
            eval_type: "system",
            dataset_ids: fullDatasetIds,
          }),
        400,
        "Compare evals-list accepted an invalid eval_type.",
      );

      const statsPayload = await client.post(
        apiPath("/model-hub/datasets/{dataset_id}/compare-stats/", {
          dataset_id: candidate.base_dataset_id,
        }),
        {
          base_column_name: candidate.base_column_name,
          dataset_ids: datasetIds,
          stat_type: "evaluation",
        },
      );
      assert(
        statsPayload && typeof statsPayload === "object",
        "Compare stats did not return an object keyed by dataset id.",
      );
      for (const datasetId of fullDatasetIds) {
        assert(
          Array.isArray(statsPayload[datasetId]),
          `Compare stats missing dataset key ${datasetId}.`,
        );
      }

      const csv = await client.post(
        apiPath("/model-hub/datasets/{dataset_id}/compare-datasets/download/", {
          dataset_id: candidate.base_dataset_id,
        }),
        {
          ...compareRequest,
          compare_id: compareId,
        },
      );
      assert(
        typeof csv === "string" && csv.includes(candidate.base_column_name),
        "Compare download did not return CSV content with the base column.",
      );

      if (candidate.eval_template_id) {
        const previewError = await expectApiErrorStatus(
          () =>
            client.post(
              apiPath("/model-hub/datasets/compare/preview-run-eval/"),
              {
                template_id: candidate.eval_template_id,
                config: { mapping: {} },
                dataset_ids: fullDatasetIds,
              },
            ),
          400,
          "Compare preview accepted a request without dataset_info.",
        );
        assert(
          /dataset/i.test(previewError.body?.detail || ""),
          "Compare preview guard did not mention dataset info.",
        );
      }

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/datasets/{dataset_id}/compare-datasets/add-eval/",
              { dataset_id: candidate.base_dataset_id },
            ),
            { dataset_ids: datasetIds },
          ),
        400,
        "Compare add-eval accepted an incomplete request.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/datasets/{dataset_id}/compare-datasets/start-eval/",
              { dataset_id: candidate.base_dataset_id },
            ),
            { user_eval_names: [], dataset_ids: datasetIds },
          ),
        400,
        "Compare start-eval accepted an empty eval list.",
      );
      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/datasets/delete-compare/{compare_id}/", {
              compare_id: compareId,
            }),
          ),
        405,
        "Compare delete route accepted GET.",
      );
      await expectApiErrorStatus(
        () =>
          client.delete(
            apiPath(
              "/model-hub/datasets/get-compare-row/{compare_id}/{row_id}/",
              {
                compare_id: compareId,
                row_id: firstRow.row_id,
              },
            ),
          ),
        405,
        "Compare row route accepted DELETE.",
      );

      const deleteResult = await client.delete(
        apiPath("/model-hub/datasets/delete-compare/{compare_id}/", {
          compare_id: compareId,
        }),
      );
      compareDeleted = true;
      assert(
        deleteResult?.message === "File(s) deleted successfully",
        "Compare delete did not return success message.",
      );

      evidence.push({
        base_dataset_id: candidate.base_dataset_id,
        other_dataset_id: candidate.other_dataset_id,
        base_column_name: candidate.base_column_name,
        compare_id: compareId,
        total_rows: Number(compare?.metadata?.total_rows || 0),
        total_pages: Number(compare?.metadata?.total_pages || 0),
        first_row_id: firstRow.row_id,
        page_two_rows: comparePageTwoRows,
        evals_returned: evals.length,
        shared_base_values: Number(candidate.shared_value_count),
        common_columns: Number(candidate.common_column_count),
        download_bytes: csv.length,
        cleanup_deleted: compareDeleted,
      });
    },
  },
  {
    id: "DPE-API-020",
    title: "Dataset row copy, merge, import, derived variables, and guards",
    tags: ["dataset", "copy", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetCopyFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Dataset copy fixture seed did not create datasets.",
      );
      cleanup.defer("hard delete API journey dataset copy fixture", () =>
        hardDeleteDatasetCopyFixture([
          fixture.source_dataset_id,
          fixture.target_dataset_id,
        ]),
      );

      const duplicate = await client.post(
        apiPath("/model-hub/datasets/{dataset_id}/duplicate-rows/", {
          dataset_id: fixture.source_dataset_id,
        }),
        { row_ids: [fixture.source_row_one_id], num_copies: 2 },
      );
      const duplicateRowIds = asArray(duplicate?.new_row_ids);
      assert(
        Number(duplicate?.total_new_rows) === 2,
        "Duplicate rows did not report two new rows.",
      );
      assert(
        duplicateRowIds.length === 2 && duplicateRowIds.every(isUuid),
        "Duplicate rows did not return two new row UUIDs.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/datasets/{dataset_id}/duplicate-rows/", {
              dataset_id: fixture.source_dataset_id,
            }),
            { row_ids: [fixture.target_low_row_id], num_copies: 1 },
          ),
        400,
        "Duplicate rows accepted a row outside the source dataset.",
      );

      const merge = await client.post(
        apiPath("/model-hub/datasets/{dataset_id}/merge/", {
          dataset_id: fixture.source_dataset_id,
        }),
        {
          target_dataset_id: fixture.target_dataset_id,
          row_ids: [fixture.source_row_two_id],
        },
      );
      assert(
        Number(merge?.rows_added) === 1,
        "Dataset merge did not report one added row.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/develops/{dataset_id}/add_rows_from_existing_dataset/",
              { dataset_id: fixture.target_dataset_id },
            ),
            {
              source_dataset_id: fixture.source_dataset_id,
              column_mapping: {
                [fixture.target_input_column_id]:
                  fixture.target_output_column_id,
              },
            },
          ),
        400,
        "Add rows from existing accepted a source column outside the source dataset.",
      );

      const imported = await client.post(
        apiPath(
          "/model-hub/develops/{dataset_id}/add_rows_from_existing_dataset/",
          { dataset_id: fixture.target_dataset_id },
        ),
        {
          source_dataset_id: fixture.source_dataset_id,
          column_mapping: {
            [fixture.source_input_column_id]: fixture.target_input_column_id,
            [fixture.source_output_column_id]: fixture.target_output_column_id,
          },
        },
      );
      assert(
        Number(imported?.rows_added) === 4,
        "Add rows from existing did not report all four source rows.",
      );

      const derivedVariables = await client.get(
        apiPath("/model-hub/datasets/{dataset_id}/derived-variables/", {
          dataset_id: fixture.source_dataset_id,
        }),
      );
      assert(
        derivedVariables &&
          typeof derivedVariables === "object" &&
          derivedVariables.derived_variables &&
          typeof derivedVariables.derived_variables === "object",
        "Dataset derived-variables endpoint did not return derived_variables.",
      );

      const audit = await loadDatasetCopyFixtureAudit(fixture);
      assertDatasetCopyFixtureAudit(audit);

      evidence.push({
        source_dataset_id: fixture.source_dataset_id,
        target_dataset_id: fixture.target_dataset_id,
        duplicate_row_ids: duplicateRowIds,
        duplicate_rows_added: Number(duplicate?.total_new_rows),
        merge_rows_added: Number(merge?.rows_added),
        imported_rows_added: Number(imported?.rows_added),
        source_row_count: Number(audit.source_row_count),
        target_row_count: Number(audit.target_row_count),
        target_max_order: Number(audit.target_max_order),
        target_order_11_input: audit.target_order_11_input,
        derived_variable_columns: Object.keys(
          derivedVariables.derived_variables,
        ).length,
      });
    },
  },
  {
    id: "DPE-API-021",
    title: "Dataset file row import, creation validation, and progress guards",
    tags: ["dataset", "file-import", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      apiBase,
      tokens,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetFileImportFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Dataset file import fixture seed did not create a dataset.",
      );
      cleanup.defer("hard delete API journey dataset file import fixture", () =>
        hardDeleteDatasetCopyFixture([fixture.dataset_id]),
      );

      const progress = await client.get(
        apiPath("/model-hub/develops/dataset-creation-progress/{dataset_id}/", {
          dataset_id: fixture.dataset_id,
        }),
      );
      assert(
        progress?.processing_status === "queued",
        "Dataset creation progress did not return seeded queued status.",
      );
      assert(
        progress?.original_filename === fixture.original_filename,
        "Dataset creation progress did not return original filename.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/develops/create-empty-dataset/"), {
            new_dataset_name: fixture.dataset_name,
            model_type: "GenerativeLLM",
            row: 0,
          }),
        400,
        "Create-empty dataset accepted a duplicate dataset name.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/develops/create-dataset-manually/"), {
            dataset_name: `api invalid manual ${runId}`,
            model_type: "GenerativeLLM",
            number_of_rows: 0,
            number_of_columns: 1,
          }),
        400,
        "Manual dataset creation accepted an invalid row count.",
      );

      await expectApiErrorStatus(
        () =>
          multipartApiRequest({
            apiBase,
            accessToken: tokens.access,
            organizationId,
            workspaceId,
            method: "POST",
            pathName: apiPath(
              "/model-hub/develops/create-dataset-from-local-file/",
            ),
            fields: {
              new_dataset_name: fixture.dataset_name,
              model_type: "GenerativeLLM",
            },
            files: [
              {
                fieldName: "file",
                fileName: "duplicate.csv",
                content: "input,file_extra\nduplicate,guard\n",
                contentType: "text/csv",
              },
            ],
          }),
        400,
        "Create dataset from local file accepted a duplicate dataset name.",
      );

      await expectApiErrorStatus(
        () =>
          multipartApiRequest({
            apiBase,
            accessToken: tokens.access,
            organizationId,
            workspaceId,
            method: "POST",
            pathName: apiPath("/model-hub/develops/add_rows_from_file/"),
            fields: { dataset_id: randomUUID() },
            files: [
              {
                fieldName: "file",
                fileName: "missing-dataset.csv",
                content: "input,file_extra\nmissing,guard\n",
                contentType: "text/csv",
              },
            ],
          }),
        404,
        "Add rows from file accepted a missing dataset id.",
      );

      const importResult = await multipartApiRequest({
        apiBase,
        accessToken: tokens.access,
        organizationId,
        workspaceId,
        method: "POST",
        pathName: apiPath("/model-hub/develops/add_rows_from_file/"),
        fields: { dataset_id: fixture.dataset_id },
        files: [
          {
            fieldName: "file",
            fileName: "dpe-api-021-import.csv",
            content: `${fixture.input_column_name},${fixture.new_column_name}\n${fixture.imported_input_one},${fixture.imported_extra_one}\n${fixture.imported_input_two},${fixture.imported_extra_two}\n`,
            contentType: "text/csv",
          },
        ],
      });
      assert(
        String(importResult).includes("2 Row(s) added successfully"),
        "Add rows from file did not report two imported rows.",
      );

      const audit = await loadDatasetFileImportFixtureAudit(fixture);
      assertDatasetFileImportFixtureAudit(audit, fixture);

      evidence.push({
        dataset_id: fixture.dataset_id,
        progress_status: progress.processing_status,
        estimated_rows: progress.estimated_rows,
        estimated_columns: progress.estimated_columns,
        row_count: Number(audit.row_count),
        column_count: Number(audit.column_count),
        max_order: Number(audit.max_order),
        imported_values: audit.imported_input_values,
        existing_blank_cells_for_new_column: Number(
          audit.existing_blank_cells_for_new_column,
        ),
        active_cell_count: Number(audit.active_cell_count),
      });
    },
  },
  {
    id: "DPE-API-022",
    title: "Dataset generated-column creation, preview, and scope guards",
    tags: ["dataset", "dynamic-columns", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetDynamicColumnFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Dynamic-column fixture seed did not create a dataset.",
      );
      cleanup.defer("hard delete API journey dynamic-column fixture", () =>
        hardDeleteDatasetCopyFixture([fixture.dataset_id]),
      );

      const preview = await client.post(
        apiPath("/model-hub/datasets/{dataset_id}/preview/{operation_type}/", {
          dataset_id: fixture.dataset_id,
          operation_type: "extract_json",
        }),
        {
          column_id: fixture.payload_column_id,
          json_key: "name",
        },
      );
      const previewResults = asArray(preview?.preview_results);
      assert(
        previewResults.length === 2,
        "Dynamic-column preview did not return the seeded sample rows.",
      );
      assert(
        previewResults
          .map((row) => row.output)
          .includes(fixture.preview_name_one),
        "Dynamic-column extract-json preview did not return the expected value.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/datasets/{dataset_id}/add-api-column/", {
              dataset_id: randomUUID(),
            }),
            {
              column_name: fixture.missing_guard_column_name,
              config: {
                url: "https://example.com",
                method: "GET",
                output_type: "string",
              },
            },
          ),
        404,
        "Dynamic add-api-column accepted a missing dataset id.",
      );

      const createdColumns = await createDatasetDynamicColumns(client, fixture);

      const audit = await loadDatasetDynamicColumnFixtureAudit(fixture);
      assertDatasetDynamicColumnFixtureAudit(audit, fixture, createdColumns);

      evidence.push({
        dataset_id: fixture.dataset_id,
        preview_outputs: previewResults.map((row) => row.output),
        dynamic_column_count: Number(audit.dynamic_column_count),
        column_order_length: Number(audit.column_order_length),
        created_columns: createdColumns,
        dynamic_sources: asArray(audit.dynamic_columns).map(
          (row) => row.source,
        ),
      });
    },
  },
  {
    id: "DPE-API-023",
    title: "Dataset generated-column config and rerun guards",
    tags: ["dataset", "dynamic-columns", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetDynamicColumnFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Dynamic-column rerun fixture seed did not create a dataset.",
      );
      cleanup.defer(
        "hard delete API journey dynamic-column rerun fixture",
        () => hardDeleteDatasetCopyFixture([fixture.dataset_id]),
      );

      const createdColumns = await createDatasetDynamicColumns(client, fixture);
      const creationAudit = await loadDatasetDynamicColumnFixtureAudit(fixture);
      assertDatasetDynamicColumnFixtureAudit(
        creationAudit,
        fixture,
        createdColumns,
      );

      const configs = {};
      for (const [name, id] of Object.entries(createdColumns)) {
        const config = await client.get(
          apiPath("/model-hub/columns/{column_id}/operation-config/", {
            column_id: id,
          }),
        );
        assert(
          config.column_id === id,
          `Operation config returned the wrong column_id for ${name}.`,
        );
        configs[name] = config.metadata;
      }

      assert(
        configs[fixture.extract_json_column_name]?.json_key === "name",
        "Extract-json operation config did not return stored json_key.",
      );
      assert(
        configs[fixture.api_column_name]?.url === "https://example.com",
        "API-call operation config did not return stored URL.",
      );
      assert(
        asArray(configs[fixture.conditional_column_name]?.config).length === 1,
        "Conditional operation config did not return stored branch config.",
      );
      assert(
        asArray(configs[fixture.classify_column_name]?.labels).length === 2,
        "Classification operation config did not return stored labels.",
      );
      assert(
        configs[fixture.entities_column_name]?.instruction === "Extract names",
        "Entity operation config did not return stored instruction.",
      );
      assert(
        configs[fixture.vector_column_name]?.sub_type === "pinecone",
        "Vector operation config did not return stored subtype.",
      );

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/columns/{column_id}/operation-config/", {
              column_id: randomUUID(),
            }),
          ),
        400,
        "Operation config accepted a missing column id.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/columns/{column_id}/rerun-operation/", {
              column_id: randomUUID(),
            }),
            { operation_type: "extract_json" },
          ),
        404,
        "Rerun operation accepted a missing column id.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/columns/{column_id}/rerun-operation/", {
              column_id: createdColumns[fixture.extract_json_column_name],
            }),
            { operation_type: "invalid_type" },
          ),
        400,
        "Rerun operation accepted an invalid operation type.",
      );

      const rerunJson = await client.post(
        apiPath("/model-hub/columns/{column_id}/rerun-operation/", {
          column_id: createdColumns[fixture.extract_json_column_name],
        }),
        { operation_type: "extract_json" },
      );
      assert(
        rerunJson.column_id ===
          createdColumns[fixture.extract_json_column_name] &&
          rerunJson.status === "running",
        "Extract-json rerun did not use stored metadata successfully.",
      );

      const rerunConditional = await client.post(
        apiPath("/model-hub/columns/{column_id}/rerun-operation/", {
          column_id: createdColumns[fixture.conditional_column_name],
        }),
        { operation_type: "conditional" },
      );
      assert(
        rerunConditional.column_id ===
          createdColumns[fixture.conditional_column_name] &&
          rerunConditional.status === "running",
        "Conditional rerun did not use stored metadata successfully.",
      );

      const rerunAudit = await loadDatasetDynamicColumnFixtureAudit(fixture);
      assertDatasetDynamicColumnFixtureAudit(
        rerunAudit,
        fixture,
        createdColumns,
      );

      evidence.push({
        dataset_id: fixture.dataset_id,
        configured_columns: Object.keys(configs).length,
        rerun_columns: [rerunJson.column_id, rerunConditional.column_id],
        dynamic_column_count: Number(rerunAudit.dynamic_column_count),
        dynamic_sources: asArray(rerunAudit.dynamic_columns).map(
          (row) => row.source,
        ),
      });
    },
  },
  {
    id: "DPE-API-024",
    title: "Dataset synthetic config update and guards",
    tags: ["dataset", "synthetic", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetSyntheticFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Synthetic dataset fixture seed did not create a dataset.",
      );
      cleanup.defer("hard delete API journey synthetic dataset fixture", () =>
        hardDeleteDatasetCopyFixture([fixture.dataset_id]),
      );

      const config = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/synthetic-config/", {
          dataset_id: fixture.dataset_id,
        }),
      );
      assert(
        config?.data?.dataset?.description === fixture.original_description,
        "Synthetic config readback did not return the seeded description.",
      );
      assert(
        Number(config?.data?.num_rows) === 10,
        "Synthetic config readback did not return the seeded row count.",
      );

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/develops/{dataset_id}/synthetic-config/", {
              dataset_id: randomUUID(),
            }),
          ),
        404,
        "Synthetic config accepted a missing dataset id.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/create-synthetic-dataset/"),
            {
              num_rows: 9,
              columns: [fixture.synthetic_column_payload],
              dataset: {
                name: `api invalid synthetic ${runId}`,
                description: "Invalid synthetic guard",
                objective: "Guard invalid synthetic creation",
                patterns: [],
              },
            },
          ),
        400,
        "Create synthetic dataset accepted fewer than ten rows.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/{dataset_id}/add_synthetic_data/", {
              dataset_id: fixture.dataset_id,
            }),
            {
              num_rows: 9,
              fill_existing_rows: false,
              columns: [fixture.synthetic_add_column_payload],
              dataset: {
                description: "Invalid synthetic add rows",
                objective: "Guard invalid synthetic add rows",
                patterns: [],
              },
            },
          ),
        400,
        "Add synthetic data accepted fewer than ten rows.",
      );

      const updatePayload = {
        num_rows: 10,
        columns: [fixture.synthetic_column_payload],
        dataset: {
          name: fixture.dataset_name,
          description: fixture.updated_description,
          objective: "Update synthetic config through API",
          patterns: [],
        },
        regenerate: false,
      };
      const update = await client.put(
        apiPath("/model-hub/develops/{dataset_id}/update-synthetic-config/", {
          dataset_id: fixture.dataset_id,
        }),
        updatePayload,
      );
      assert(
        update?.data?.dataset_id === fixture.dataset_id,
        "Synthetic config update returned the wrong dataset id.",
      );

      const updatedConfig = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/synthetic-config/", {
          dataset_id: fixture.dataset_id,
        }),
      );
      assert(
        updatedConfig?.data?.dataset?.description ===
          fixture.updated_description,
        "Synthetic config update did not persist the new description.",
      );

      const audit = await loadDatasetSyntheticFixtureAudit(fixture);
      assertDatasetSyntheticFixtureAudit(audit, fixture);

      evidence.push({
        dataset_id: fixture.dataset_id,
        initial_description: config.data.dataset.description,
        updated_description: updatedConfig.data.dataset.description,
        row_count: Number(audit.row_count),
        column_count: Number(audit.column_count),
        cell_count: Number(audit.cell_count),
        column_order_length: Number(audit.column_order_length),
      });
    },
  },
  {
    id: "DPE-API-025",
    title: "Dataset explanation summary refresh guards",
    tags: ["dataset", "summary", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedDatasetExplanationFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Explanation summary fixture seed did not create a dataset.",
      );
      cleanup.defer("hard delete API journey explanation summary fixture", () =>
        hardDeleteDatasetCopyFixture([fixture.dataset_id]),
      );

      const summary = await client.get(
        apiPath("/model-hub/datasets/explanation-summary/{dataset_id}/", {
          dataset_id: fixture.dataset_id,
        }),
      );
      assert(
        summary.status === "insufficient_data" &&
          Number(summary.row_count) === fixture.row_count,
        "Explanation summary read did not return insufficient_data for the seeded small dataset.",
      );

      const refreshed = await client.post(
        apiPath(
          "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/",
          {
            dataset_id: fixture.dataset_id,
          },
        ),
        {},
      );
      assert(
        refreshed.status === "insufficient_data" &&
          Number(refreshed.row_count) === fixture.row_count,
        "Explanation summary refresh did not preserve insufficient_data status.",
      );

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/datasets/explanation-summary/{dataset_id}/", {
              dataset_id: randomUUID(),
            }),
          ),
        404,
        "Explanation summary read accepted a missing dataset id.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/datasets/explanation-summary/{dataset_id}/refresh/",
              { dataset_id: randomUUID() },
            ),
            {},
          ),
        404,
        "Explanation summary refresh accepted a missing dataset id.",
      );

      const audit = await loadDatasetExplanationFixtureAudit(fixture);
      assertDatasetExplanationFixtureAudit(audit, fixture);

      evidence.push({
        dataset_id: fixture.dataset_id,
        row_count: Number(summary.row_count),
        read_status: summary.status,
        refresh_status: refreshed.status,
        db_status: audit.eval_reason_status,
      });
    },
  },
  {
    id: "DPE-API-026",
    title: "Hugging Face dataset lookup and config",
    tags: ["dataset", "huggingface", "safe"],
    async run({ client, evidence }) {
      const datasetId = "rajpurkar/squad";
      const list = await client.post(
        apiPath("/model-hub/datasets/huggingface/list/"),
        {
          search_query: "squad",
          filter_params: { sort: "downloads" },
        },
      );
      const datasets = asArray(list?.datasets);
      assert(
        Number(list?.total_datasets) > 0 && datasets.length > 0,
        "Hugging Face list did not return datasets.",
      );
      assert(
        datasets.some((dataset) => dataset.id === datasetId),
        "Hugging Face list did not include rajpurkar/squad.",
      );

      const detail = await client.post(
        apiPath("/model-hub/datasets/huggingface/detail/"),
        { dataset_id: datasetId },
      );
      assert(
        detail?.dataset?.id === datasetId &&
          asArray(detail?.dataset?.tags).length > 0,
        "Hugging Face detail did not return the expected dataset metadata.",
      );

      const config = await client.post(
        apiPath("/model-hub/develops/get-huggingface-dataset-config/"),
        { dataset_path: datasetId },
      );
      const splits = config?.dataset_info?.splits || {};
      assert(
        asArray(splits.plain_text).includes("train") &&
          asArray(splits.plain_text).includes("validation"),
        "Hugging Face config did not return plain_text train/validation splits.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/get-huggingface-dataset-config/"),
            { dataset_path: "lhoestq/custom_squad" },
          ),
        400,
        "Hugging Face config accepted a dataset with arbitrary code.",
      );

      evidence.push({
        dataset_id: datasetId,
        total_datasets: Number(list.total_datasets),
        list_result_count: datasets.length,
        detail_downloads: Number(detail.dataset.downloads || 0),
        config_names: Object.keys(splits),
        plain_text_splits: splits.plain_text,
      });
    },
  },
  {
    id: "DPE-API-027",
    title: "Dataset eval drawer list, preview, run, stop, and cleanup",
    tags: ["dataset", "evals", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip(
          "System eval word_count_in_range was not available for dataset evals.",
        );
      }
      const requiredKeys = promptEvalRequiredKeys(template);
      if (!requiredKeys.includes("text")) {
        skip("word_count_in_range did not expose the expected text input key.");
      }
      const paramsConfig = promptEvalParamsForTemplate(template);
      const evalConfig = {
        mapping: { text: null },
        params: paramsConfig.params,
        reason_column: true,
      };
      const fixture = await seedDatasetEvalDrawerFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Dataset eval drawer fixture seed did not create a dataset.",
      );
      evalConfig.mapping.text = fixture.input_column_id;
      cleanup.defer("hard delete API journey dataset eval drawer fixture", () =>
        hardDeleteDatasetEvalDrawerFixture(fixture),
      );

      const systemList = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/get_evals_list/", {
          dataset_id: fixture.dataset_id,
        }),
        {
          query: {
            eval_categories: "futureagi_built",
            search_text: template.name,
          },
        },
      );
      const systemRows = payloadArray(systemList?.evals, "evals");
      assert(
        systemRows.some(
          (row) => row.id === template.id && row.name === template.name,
        ),
        "Dataset eval list did not include the expected system eval template.",
      );

      const presetStructure = await client.get(
        apiPath(
          "/model-hub/develops/{dataset_id}/get_eval_structure/{eval_id}/",
          { dataset_id: fixture.dataset_id, eval_id: template.id },
        ),
        { query: { eval_type: "preset" } },
      );
      assertDatasetEvalStructure(presetStructure, {
        templateId: template.id,
        expectedName: template.name,
        expectedMapping: { text: "" },
      });

      const preview = await client.post(
        apiPath("/model-hub/develops/{dataset_id}/preview_run_eval/", {
          dataset_id: fixture.dataset_id,
        }),
        {
          template_id: template.id,
          config: evalConfig,
          model: "turing_small",
        },
      );
      const previewResponses = payloadArray(preview?.responses, "responses");
      assert(
        previewResponses.length === fixture.row_count,
        "Dataset eval preview did not evaluate the seeded fixture rows.",
      );
      assert(
        previewResponses.every(
          (response) => normalizeEvalOutput(response?.output) === "passed",
        ),
        "Dataset eval preview did not pass all seeded word-count rows.",
      );

      const metricName = `api_eval_drawer_${suffix}`;
      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_user_eval/", {
          dataset_id: fixture.dataset_id,
        }),
        {
          name: metricName,
          template_id: template.id,
          config: evalConfig,
          run: false,
          model: "turing_small",
          error_localizer: false,
        },
      );

      const userList = await client.get(
        apiPath("/model-hub/develops/{dataset_id}/get_evals_list/", {
          dataset_id: fixture.dataset_id,
        }),
        { query: { eval_type: "user", search_text: metricName } },
      );
      const userRows = payloadArray(userList?.evals, "evals");
      const userEval = userRows.find((row) => row.name === metricName);
      assert(
        isUuid(userEval?.id),
        "Dataset eval list did not return the new metric.",
      );
      const metricId = userEval.id;

      const userStructure = await client.get(
        apiPath(
          "/model-hub/develops/{dataset_id}/get_eval_structure/{eval_id}/",
          { dataset_id: fixture.dataset_id, eval_id: metricId },
        ),
        { query: { eval_type: "user" } },
      );
      assertDatasetEvalStructure(userStructure, {
        metricId,
        templateId: template.id,
        expectedName: metricName,
        expectedMapping: { text: fixture.input_column_id },
      });

      let activeAudit = await loadDatasetEvalDrawerDbAudit({
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertDatasetEvalDrawerDbAudit(activeAudit, {
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricId,
        metricName,
        organizationId,
        workspaceId,
        expectedStatus: "Inactive",
        expectedDeleted: false,
        expectedEvalColumns: 0,
      });

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/start_evals_process/", {
          dataset_id: fixture.dataset_id,
        }),
        { user_eval_ids: [metricId] },
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/{dataset_id}/start_evals_process/", {
              dataset_id: fixture.dataset_id,
            }),
            { user_eval_ids: [randomUUID()] },
          ),
        404,
        "Dataset eval start accepted an eval id outside the dataset/workspace.",
      );

      const startedAudit = await loadDatasetEvalDrawerDbAudit({
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertDatasetEvalDrawerDbAudit(startedAudit, {
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricId,
        metricName,
        organizationId,
        workspaceId,
        expectedStatus: "NotStarted",
        expectedDeleted: false,
        expectedEvalColumns: 1,
        expectedReasonColumns: 1,
      });

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/stop_user_eval/{eval_id}/", {
          dataset_id: fixture.dataset_id,
          eval_id: metricId,
        }),
        {},
      );
      const stoppedAudit = await loadDatasetEvalDrawerDbAudit({
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertDatasetEvalDrawerDbAudit(stoppedAudit, {
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricId,
        metricName,
        organizationId,
        workspaceId,
        expectedStatus: "Error",
        expectedDeleted: false,
        expectedEvalColumns: 1,
        expectedReasonColumns: 1,
      });

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          {
            dataset_id: fixture.dataset_id,
            eval_id: metricId,
          },
        ),
        { body: { delete_column: true } },
      );
      const deletedMetricAudit = await loadDatasetEvalDrawerDbAudit({
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertDatasetEvalDrawerDbAudit(deletedMetricAudit, {
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricId,
        metricName,
        organizationId,
        workspaceId,
        expectedStatus: "Error",
        expectedDeleted: true,
        expectedEvalColumns: 0,
        expectedReasonColumns: 0,
        expectedDeletedEvalColumns: 1,
        expectedDeletedReasonColumns: 1,
      });

      const templateName = `api_journey_eval_drawer_template_${suffix}`;
      const createdTemplate = await client.post(
        apiPath("/model-hub/create_custom_evals/"),
        {
          name: templateName,
          description:
            "Custom eval template for dataset drawer delete coverage.",
          template_type: "Futureagi",
          output_type: "Pass/Fail",
          required_keys: ["response"],
          criteria: "Judge {{response}} for clarity.",
          tags: ["api-journey", "dataset-eval-drawer"],
          config: {
            model: "turing_large",
            proxy_agi: true,
            visible_ui: true,
          },
          check_internet: false,
        },
      );
      const templateId =
        createdTemplate?.result?.eval_template_id ||
        createdTemplate?.data?.result?.eval_template_id ||
        createdTemplate?.eval_template_id;
      assert(
        isUuid(templateId),
        "Dataset eval drawer custom template create did not return a UUID.",
      );
      let templateDeleted = false;
      cleanup.defer("delete API journey dataset eval drawer template", () => {
        if (templateDeleted) return null;
        return client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        });
      });

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_template_eval/{eval_id}/",
          { dataset_id: fixture.dataset_id, eval_id: templateId },
        ),
      );
      templateDeleted = true;
      const deletedTemplateAudit = await loadCustomEvalCreateDbAudit({
        templateId,
        organizationId,
        workspaceId,
      });
      assert(
        deletedTemplateAudit.deleted === true &&
          deletedTemplateAudit.deleted_at_set === true,
        "Dataset eval drawer template delete did not stamp deleted_at.",
      );

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath(
              "/model-hub/develops/{dataset_id}/get_eval_structure/{eval_id}/",
              { dataset_id: randomUUID(), eval_id: template.id },
            ),
            { query: { eval_type: "preset" } },
          ),
        404,
        "Dataset eval structure accepted an inaccessible dataset id.",
      );

      activeAudit = await loadDatasetEvalDrawerDbAudit({
        datasetId: fixture.dataset_id,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      evidence.push({
        dataset_id: fixture.dataset_id,
        template_id: template.id,
        user_eval_metric_id: metricId,
        preview_responses: previewResponses.length,
        status_after_start: startedAudit.metric_status,
        status_after_stop: stoppedAudit.metric_status,
        metric_deleted_at_set: deletedMetricAudit.metric_deleted_at_set,
        deleted_eval_column_count: Number(
          deletedMetricAudit.deleted_eval_column_count,
        ),
        deleted_reason_column_count: Number(
          deletedMetricAudit.deleted_reason_column_count,
        ),
        deleted_template_id: templateId,
        deleted_template_deleted_at_set: deletedTemplateAudit.deleted_at_set,
        active_metric_count_after_delete: Number(
          activeAudit.active_metric_count,
        ),
      });
    },
  },
  {
    id: "DPE-API-028",
    title: "Legacy experiment row diff scope and payload",
    tags: ["experiments", "dataset", "guard", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedLegacyExperimentRowDiffFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Legacy row-diff fixture seed did not create an experiment.",
      );
      cleanup.defer("hard delete API journey legacy row-diff fixture", () =>
        hardDeleteLegacyExperimentRowDiffFixture(fixture),
      );

      const diffPayload = await client.post(
        apiPath("/model-hub/develops/get-row-diff/"),
        {
          experiment_id: fixture.experiment_id,
          column_ids: [fixture.base_column_id, fixture.other_column_id],
          row_ids: [fixture.row_id],
          compare_column_ids: [fixture.base_column_id, fixture.other_column_id],
        },
      );
      const rowPayload = diffPayload?.[fixture.row_id];
      assert(rowPayload, "Legacy row-diff did not return the requested row.");
      assert(
        rowPayload?.[fixture.base_column_id]?.cell_value ===
          fixture.base_cell_value,
        "Legacy row-diff returned the wrong base cell value.",
      );
      assert(
        rowPayload?.[fixture.base_column_id]?.cell_diff_value == null,
        "Legacy row-diff returned a diff for the base column.",
      );
      assert(
        rowPayload?.[fixture.other_column_id]?.cell_value ===
          fixture.other_cell_value,
        "Legacy row-diff returned the wrong comparison cell value.",
      );
      const diffStatuses = asArray(
        rowPayload?.[fixture.other_column_id]?.cell_diff_value,
      ).map((part) => part.status);
      assert(
        diffStatuses.includes("removed") && diffStatuses.includes("added"),
        "Legacy row-diff did not include removed and added diff segments.",
      );
      assert(
        rowPayload?.[fixture.other_column_id]?.value_infos?.metadata?.prompt ===
          "other",
        "Legacy row-diff did not preserve JSON value_infos.",
      );

      const outsideError = await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/develops/get-row-diff/"), {
            experiment_id: fixture.experiment_id,
            column_ids: [fixture.base_column_id, fixture.outside_column_id],
            row_ids: [fixture.outside_row_id],
            compare_column_ids: [
              fixture.base_column_id,
              fixture.outside_column_id,
            ],
          }),
        400,
        "Legacy row-diff accepted rows or columns outside the experiment dataset.",
      );
      assert(
        !JSON.stringify(outsideError.body || {}).includes(
          fixture.outside_cell_value,
        ),
        "Legacy row-diff outside-dataset error leaked the outside cell value.",
      );

      let otherWorkspaceGuard = "skipped:no-other-workspace";
      const otherWorkspaceId = await findOtherWorkspaceId(
        organizationId,
        workspaceId,
      );
      if (otherWorkspaceId) {
        const otherFixture = await seedLegacyExperimentRowDiffFixture({
          runId: `${runId}-otherws`,
          organizationId,
          workspaceId: otherWorkspaceId,
        });
        cleanup.defer(
          "hard delete API journey other-workspace legacy row-diff fixture",
          () => hardDeleteLegacyExperimentRowDiffFixture(otherFixture),
        );
        await expectApiErrorStatus(
          () =>
            client.post(apiPath("/model-hub/develops/get-row-diff/"), {
              experiment_id: otherFixture.experiment_id,
              column_ids: [
                otherFixture.base_column_id,
                otherFixture.other_column_id,
              ],
              row_ids: [otherFixture.row_id],
              compare_column_ids: [
                otherFixture.base_column_id,
                otherFixture.other_column_id,
              ],
            }),
          404,
          "Legacy row-diff accepted a same-org experiment from another workspace.",
        );
        otherWorkspaceGuard = "passed";
      }

      evidence.push({
        experiment_id: fixture.experiment_id,
        dataset_id: fixture.dataset_id,
        row_id: fixture.row_id,
        base_column_id: fixture.base_column_id,
        other_column_id: fixture.other_column_id,
        outside_dataset_id: fixture.outside_dataset_id,
        diff_segment_count: diffStatuses.length,
        outside_guard_status: outsideError.status,
        other_workspace_guard: otherWorkspaceGuard,
      });
    },
  },
  {
    id: "DPE-API-029",
    title: "Hugging Face create and add-rows validation guards",
    tags: ["dataset", "huggingface", "guard", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedHuggingFaceGuardFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Hugging Face guard fixture seed did not create datasets.",
      );
      cleanup.defer("hard delete API journey Hugging Face guard fixture", () =>
        hardDeleteHuggingFaceGuardFixture(fixture),
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/create-dataset-from-huggingface/"),
            {
              name: fixture.duplicate_dataset_name,
              model_type: "GenerativeLLM",
              num_rows: 1,
              huggingface_dataset_name: "rajpurkar/squad",
              huggingface_dataset_config: "plain_text",
              huggingface_dataset_split: "train",
            },
          ),
        400,
        "Hugging Face create accepted a duplicate active dataset name.",
      );

      const afterDuplicateAudit = await loadHuggingFaceGuardFixtureAudit(
        fixture,
        organizationId,
        workspaceId,
      );
      assert(
        Number(afterDuplicateAudit.duplicate_name_active_count) === 1,
        "Duplicate Hugging Face create produced an extra active dataset.",
      );

      let otherWorkspaceGuard = "skipped:no-other-workspace";
      if (fixture.other_dataset_id) {
        await expectApiErrorStatus(
          () =>
            client.post(
              apiPath(
                "/model-hub/develops/{dataset_id}/add_rows_from_huggingface/",
                { dataset_id: fixture.other_dataset_id },
              ),
              {
                num_rows: 1,
                huggingface_dataset_name: "rajpurkar/squad",
                huggingface_dataset_config: "plain_text",
                huggingface_dataset_split: "train",
              },
            ),
          404,
          "Hugging Face add-rows accepted a same-org other-workspace dataset.",
        );
        otherWorkspaceGuard = "passed";
      }

      const finalAudit = await loadHuggingFaceGuardFixtureAudit(
        fixture,
        organizationId,
        workspaceId,
      );
      if (fixture.other_dataset_id) {
        assert(
          Number(finalAudit.other_dataset_row_count) ===
            Number(fixture.other_initial_row_count),
          "Other-workspace Hugging Face add-rows mutated row count.",
        );
        assert(
          Number(finalAudit.other_dataset_column_count) ===
            Number(fixture.other_initial_column_count),
          "Other-workspace Hugging Face add-rows mutated column count.",
        );
      }

      evidence.push({
        duplicate_dataset_id: fixture.duplicate_dataset_id,
        duplicate_dataset_name: fixture.duplicate_dataset_name,
        duplicate_name_active_count: Number(
          finalAudit.duplicate_name_active_count,
        ),
        other_workspace_guard: otherWorkspaceGuard,
        other_dataset_id: fixture.other_dataset_id,
        other_dataset_row_count: Number(
          finalAudit.other_dataset_row_count || 0,
        ),
        other_dataset_column_count: Number(
          finalAudit.other_dataset_column_count || 0,
        ),
      });
    },
  },
  {
    id: "DPE-API-030",
    title: "Experiment dataset table readback and materialize guards",
    tags: ["dataset", "experiment", "guard", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedExperimentDatasetMaterializeFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Experiment dataset materialize fixture seed did not create records.",
      );
      cleanup.defer(
        "hard delete API journey experiment dataset materialize fixture",
        () => hardDeleteExperimentDatasetMaterializeFixture(fixture),
      );

      const table = await client.get(
        apiPath(
          "/model-hub/develops/{experiment_dataset_id}/get-experiment-dataset-table/",
          { experiment_dataset_id: fixture.experiment_dataset_id },
        ),
        { query: { page_size: 1, current_page_index: 0 } },
      );
      assert(
        table?.metadata?.dataset_name === fixture.experiment_dataset_name,
        "Experiment dataset table returned the wrong dataset name.",
      );
      assert(
        table.metadata.experiment_id === fixture.experiment_id,
        "Experiment dataset table metadata did not expose the parent experiment id.",
      );
      assert(
        table.metadata.experiment_name === fixture.experiment_name,
        "Experiment dataset table metadata did not expose the parent experiment name.",
      );
      assert(
        Number(table.metadata.total_rows) === 2,
        "Experiment dataset table total_rows did not include all rows.",
      );
      assert(
        Number(table.metadata.total_pages) === 2,
        "Experiment dataset table total_pages did not use the unsliced row count.",
      );
      assert(
        Array.isArray(table.table) && table.table.length === 1,
        "Experiment dataset table page did not return exactly one row.",
      );
      assert(
        table.table[0]?.row_id === fixture.first_row_id,
        "Experiment dataset table did not preserve row ordering.",
      );
      assert(
        table.table[0]?.[fixture.input_column_id]?.cell_value ===
          fixture.first_input_value,
        "Experiment dataset table did not include the base input cell.",
      );
      assert(
        table.table[0]?.[fixture.result_column_id]?.cell_value ===
          fixture.first_result_value,
        "Experiment dataset table did not include the experiment result cell.",
      );
      const columnNames = new Set(
        asArray(table.column_config).map((column) => column.name),
      );
      assert(
        columnNames.has("api_exp_input") && columnNames.has("api_exp_result"),
        "Experiment dataset table did not expose both base and experiment columns.",
      );

      const [derivedDatasets, summary] = await Promise.all([
        client.get(
          apiPath("/model-hub/develops/get-derived-datasets/{dataset_id}/", {
            dataset_id: fixture.dataset_id,
          }),
        ),
        client.get(
          apiPath("/model-hub/experiments/v2/{experiment_id}/stats/", {
            experiment_id: fixture.experiment_id,
          }),
        ),
      ]);
      const derivedRow = asArray(derivedDatasets).find(
        (row) => row?.id === fixture.experiment_dataset_id,
      );
      assert(
        derivedRow?.name === fixture.experiment_dataset_name,
        "Derived datasets endpoint did not include the experiment dataset row.",
      );
      assert(
        derivedRow?.experiment?.id === fixture.experiment_id,
        "Derived datasets endpoint did not expose the parent experiment id.",
      );
      assertExperimentStatsPayload(summary);
      assert(
        asArray(summary.table_data).some(
          (row) => row?.dataset_id === fixture.experiment_dataset_id,
        ),
        "Individual experiment summary did not include the experiment dataset row.",
      );
      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/experiments/v2/{experiment_id}/stats/", {
              experiment_id: fixture.experiment_dataset_id,
            }),
          ),
        400,
        "Experiment summary accepted an experiment dataset id as an experiment id.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/develops/{exp_dataset_id}/create-dataset/", {
              exp_dataset_id: fixture.experiment_dataset_id,
            }),
            {
              name: fixture.duplicate_dataset_name,
              model_type: "GenerativeLLM",
            },
          ),
        400,
        "Experiment create-dataset accepted a duplicate active dataset name.",
      );

      let otherWorkspaceGuard = "skipped:no-other-workspace";
      if (fixture.other_experiment_dataset_id) {
        await Promise.all([
          expectApiErrorStatus(
            () =>
              client.get(
                apiPath(
                  "/model-hub/develops/{experiment_dataset_id}/get-experiment-dataset-table/",
                  {
                    experiment_dataset_id: fixture.other_experiment_dataset_id,
                  },
                ),
              ),
            404,
            "Experiment dataset table read accepted a same-org other-workspace fixture.",
          ),
          expectApiErrorStatus(
            () =>
              client.get(
                apiPath("/model-hub/develops/get-derived-datasets/{dataset_id}/", {
                  dataset_id: fixture.other_dataset_id,
                }),
              ),
            404,
            "Derived datasets endpoint exposed a same-org other-workspace dataset.",
          ),
          expectApiErrorStatus(
            () =>
              client.post(
                apiPath("/model-hub/develops/{exp_dataset_id}/create-dataset/", {
                  exp_dataset_id: fixture.other_experiment_dataset_id,
                }),
                {
                  name: fixture.other_blocked_dataset_name,
                  model_type: "GenerativeLLM",
                },
              ),
            404,
            "Experiment create-dataset accepted a same-org other-workspace fixture.",
          ),
        ]);
        otherWorkspaceGuard = "passed";
      }

      const audit = await loadExperimentDatasetMaterializeAudit(fixture);
      assert(
        Number(audit.duplicate_name_active_count) === 1,
        "Experiment create-dataset duplicate guard produced an extra dataset.",
      );
      assert(
        Number(audit.blocked_dataset_count) === 0,
        "Experiment create-dataset other-workspace guard created a dataset.",
      );

      evidence.push({
        experiment_dataset_id: fixture.experiment_dataset_id,
        experiment_dataset_name: fixture.experiment_dataset_name,
        table_total_rows: Number(table.metadata.total_rows),
        table_total_pages: Number(table.metadata.total_pages),
        derived_dataset_count: asArray(derivedDatasets).length,
        summary_rows: asArray(summary.table_data).length,
        duplicate_dataset_name: fixture.duplicate_dataset_name,
        duplicate_name_active_count: Number(audit.duplicate_name_active_count),
        other_workspace_guard: otherWorkspaceGuard,
        other_experiment_dataset_id: fixture.other_experiment_dataset_id,
        blocked_dataset_count: Number(audit.blocked_dataset_count),
      });
    },
  },
  {
    id: "PROMPT-API-002",
    title:
      "Dataset run-prompt preview, create, config reload, row rerun, and guards",
    tags: ["prompts", "dataset", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const datasetName = `api journey run prompt ${runId}`;
      const inputName = `api_rp_input_${suffix}`;
      const outputName = `api_rp_output_${suffix}`;
      const inputValue = `Return OK for ${runId}`;
      const rowId = randomUUID();
      const deletedColumnIds = new Set();
      let rowDeleted = false;

      const runPromptOptions = await client.get(
        apiPath("/model-hub/develops/retrieve_run_prompt_options/"),
      );
      const model = selectAvailableRunPromptModel(runPromptOptions);
      if (!model) {
        skip("No available run-prompt model found for prompt execution.");
      }

      const dataset = await createOrResolveWritableDataset(
        client,
        cleanup,
        datasetName,
        evidence,
      );
      const datasetId = dataset.id;
      cleanup.defer("delete run-prompt row", async () => {
        if (rowDeleted) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
              dataset_id: datasetId,
            }),
            { body: { row_ids: [rowId], selected_all_rows: false } },
          ),
        );
      });

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
          dataset_id: datasetId,
        }),
        {
          new_column_name: inputName,
          column_type: "text",
          source: "OTHERS",
        },
      );
      const table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const inputColumn = findColumn(table, inputName);
      assert(inputColumn?.id, "Run-prompt input column was not visible.");
      cleanup.defer("delete run-prompt input column", async () => {
        if (deletedColumnIds.has(inputColumn.id)) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
              { dataset_id: datasetId, column_id: inputColumn.id },
            ),
          ),
        );
      });

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_rows/", {
          dataset_id: datasetId,
        }),
        {
          rows: [
            {
              id: rowId,
              cells: [{ column_name: inputName, value: inputValue }],
            },
          ],
        },
      );

      const config = runPromptColumnConfig(model.model_name, inputName);
      const preview = await client.post(
        apiPath("/model-hub/develops/preview_run_prompt_column/"),
        {
          dataset_id: datasetId,
          name: outputName,
          first_n_rows: 1,
          config,
        },
      );
      assertRunPromptPreview(preview);

      await client.post(apiPath("/model-hub/develops/add_run_prompt_column/"), {
        dataset_id: datasetId,
        name: outputName,
        config,
      });

      const firstRun = await waitForRunPromptColumnCompletion(client, {
        datasetId,
        rowId,
        columnName: outputName,
      });
      cleanup.defer("delete run-prompt output column", async () => {
        if (deletedColumnIds.has(firstRun.column.id)) return;
        await ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
              { dataset_id: datasetId, column_id: firstRun.column.id },
            ),
          ),
        );
      });

      const activeAudit = await loadRunPromptColumnDbAudit({
        datasetId,
        rowId,
        columnId: firstRun.column.id,
        organizationId,
        workspaceId,
      });
      assertRunPromptColumnDbAudit(activeAudit, {
        datasetId,
        rowId,
        columnId: firstRun.column.id,
        outputName,
        modelName: model.model_name,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      const runPromptId = activeAudit.run_prompt_id;
      const reloadedConfig = await client.get(
        apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
        { query: { column_id: firstRun.column.id } },
      );
      assertRunPromptConfigReload(reloadedConfig, {
        datasetId,
        outputName,
        modelName: model.model_name,
        inputName,
      });

      await client.post(apiPath("/model-hub/run-prompt-for-rows/"), {
        run_prompt_ids: [runPromptId],
        row_ids: [rowId],
      });
      const rerun = await waitForRunPromptColumnCompletion(client, {
        datasetId,
        rowId,
        columnName: outputName,
      });
      assert(
        rerun.column.id === firstRun.column.id,
        "Row rerun changed the run-prompt output column id.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/run-prompt-for-rows/"), {
            run_prompt_ids: [runPromptId],
            row_ids: [randomUUID()],
          }),
        404,
        "run-prompt-for-rows accepted a row outside the run-prompt dataset.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/run-prompt/"), {
            dataset_id: randomUUID(),
            name: `api direct run prompt guard ${runId}`,
            model: model.model_name,
            messages: [{ role: "user", content: "Hello" }],
          }),
        404,
        "direct run-prompt did not return 404 for an inaccessible dataset.",
      );

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
          dataset_id: datasetId,
        }),
        { body: { row_ids: [rowId], selected_all_rows: false } },
      );
      rowDeleted = true;

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
          dataset_id: datasetId,
          column_id: firstRun.column.id,
        }),
      );
      deletedColumnIds.add(firstRun.column.id);
      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
          dataset_id: datasetId,
          column_id: inputColumn.id,
        }),
      );
      deletedColumnIds.add(inputColumn.id);

      const deletedAudit = await loadRunPromptColumnDbAudit({
        datasetId,
        rowId,
        columnId: firstRun.column.id,
        organizationId,
        workspaceId,
      });
      assertRunPromptColumnDbAudit(deletedAudit, {
        datasetId,
        rowId,
        columnId: firstRun.column.id,
        outputName,
        modelName: model.model_name,
        organizationId,
        workspaceId,
        expectedDeleted: true,
      });

      evidence.push({
        dataset_id: datasetId,
        dataset_source: dataset.source,
        row_id: rowId,
        input_column_id: inputColumn.id,
        output_column_id: firstRun.column.id,
        run_prompt_id: runPromptId,
        model: model.model_name,
        preview_responses: preview.responses.length,
        first_run_status: firstRun.status,
        rerun_status: rerun.status,
        run_prompt_deleted_at_set: deletedAudit.run_prompt_deleted_at_set,
        output_column_deleted_at_set: deletedAudit.column_deleted_at_set,
        active_cells_after_cleanup: Number(deletedAudit.active_cell_count),
      });
    },
  },
  {
    id: "PROMPT-API-005",
    title: "Dataset run-prompt edit and config scope guards",
    tags: ["prompts", "dataset", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const fixture = await seedRunPromptEditFixture({
        runId,
        organizationId,
        workspaceId,
      });
      assert(
        fixture?.fixture_created,
        "Run-prompt edit fixture seed did not create a dataset.",
      );
      cleanup.defer("hard delete API journey run-prompt edit fixture", () =>
        hardDeleteRunPromptEditFixture(fixture),
      );

      const initialConfig = await client.get(
        apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
        { query: { column_id: fixture.output_column_id } },
      );
      assertRunPromptConfigReload(initialConfig, {
        datasetId: fixture.dataset_id,
        outputName: fixture.output_column_name,
        modelName: fixture.model_name,
        inputName: fixture.input_column_name,
      });

      const editConfig = runPromptColumnConfig(
        fixture.model_name,
        fixture.input_column_name,
      );
      editConfig.messages[0].content[0].text =
        "Return exactly EDITED OK. Do not include punctuation.";
      const editResult = await client.post(
        apiPath("/model-hub/develops/edit_run_prompt_column/"),
        {
          dataset_id: fixture.dataset_id,
          column_id: fixture.output_column_id,
          name: fixture.edited_output_column_name,
          config: editConfig,
        },
      );
      assert(
        String(editResult).includes("updated successfully"),
        "Run-prompt edit did not return a success message.",
      );

      const editedConfig = await client.get(
        apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
        { query: { column_id: fixture.output_column_id } },
      );
      assertRunPromptConfigReload(editedConfig, {
        datasetId: fixture.dataset_id,
        outputName: fixture.edited_output_column_name,
        modelName: fixture.model_name,
        inputName: fixture.input_column_name,
      });

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/develops/retrieve_run_prompt_column_config/"),
            { query: { column_id: randomUUID() } },
          ),
        404,
        "Run-prompt config accepted a missing column id.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/develops/edit_run_prompt_column/"), {
            dataset_id: randomUUID(),
            column_id: fixture.output_column_id,
            name: `missing ${fixture.edited_output_column_name}`,
            config: editConfig,
          }),
        404,
        "Run-prompt edit accepted a missing dataset id.",
      );

      const audit = await loadRunPromptColumnDbAudit({
        datasetId: fixture.dataset_id,
        rowId: fixture.row_id,
        columnId: fixture.output_column_id,
        organizationId,
        workspaceId,
      });
      assertRunPromptColumnDbAudit(audit, {
        datasetId: fixture.dataset_id,
        rowId: fixture.row_id,
        columnId: fixture.output_column_id,
        outputName: fixture.edited_output_column_name,
        modelName: fixture.model_name,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      evidence.push({
        dataset_id: fixture.dataset_id,
        column_id: fixture.output_column_id,
        run_prompt_id: fixture.run_prompt_id,
        original_name: fixture.output_column_name,
        edited_name: fixture.edited_output_column_name,
        run_prompt_status: audit.run_prompt_status,
        active_cell_count: Number(audit.active_cell_count),
      });
    },
  },
  {
    id: "KB-API-001",
    title: "Knowledge base SDK snippets use placeholder credentials",
    tags: ["knowledge-base", "safe", "credential-safety", "db-audit"],
    async run({ client, evidence, user, organizationId, runId }) {
      const email = currentUserEmail(user);
      if (!email) skip("Current user email was not available for DB audit.");

      const beforeAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );
      let createResult;
      let updateResult;
      try {
        createResult = await client.get(
          apiPath("/model-hub/knowledge-base/"),
          { query: { type: "create", name: `api journey kb ${runId}` } },
        );
        updateResult = await client.get(
          apiPath("/model-hub/knowledge-base/"),
          { query: { type: "update", name: `api journey kb ${runId}` } },
        );
      } catch (error) {
        if (isLegacyKnowledgeBaseEntitlementDeniedError(error)) {
          evidence.push({
            mode: "legacy_entitlement_denied",
            status: error.status,
            body: error.body,
          });
          skip(
            "Legacy knowledge-base SDK endpoint is entitlement-blocked in this local workspace; KB-API-004 covers the current gate.",
          );
        }
        throw error;
      }
      const afterAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );

      assertKnowledgeBaseSdkSnippetSafety(createResult?.code, "create");
      assertKnowledgeBaseSdkSnippetSafety(updateResult?.code, "update");
      assert(
        Number(afterAudit.user_key_count) ===
          Number(beforeAudit.user_key_count),
        "knowledge-base SDK code generation created or deleted a user API key.",
      );

      evidence.push({
        create_code_length: createResult.code.length,
        update_code_length: updateResult.code.length,
        user_key_count_before: Number(beforeAudit.user_key_count),
        user_key_count_after: Number(afterAudit.user_key_count),
        placeholders_present: true,
      });
    },
  },
  {
    id: "KB-API-002",
    title: "Legacy knowledge base create, list, files, guards, and delete",
    tags: ["knowledge-base", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      evidence,
      apiBase,
      tokens,
      organizationId,
      workspaceId,
      runId,
    }) {
      requireMutations();
      await skipIfLegacyKnowledgeBaseEntitlementDenied(client, evidence);
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const kbName = `api_journey_kb_${suffix}`;
      const fileName = `api_journey_kb_${suffix}.txt`;
      let kbId = "";
      let kbDeleted = false;

      const created = await multipartApiRequest({
        apiBase,
        accessToken: tokens.access,
        organizationId,
        workspaceId,
        method: "POST",
        pathName: apiPath("/model-hub/knowledge-base/"),
        fields: { name: kbName },
        files: [
          {
            fieldName: "file",
            fileName,
            contentType: "text/plain",
            content: `Knowledge base API journey fixture ${runId}\n`,
          },
        ],
      });
      kbId = created?.kb_id;
      const fileIds = asArray(created?.file_ids);
      assert(
        isUuid(kbId),
        "Knowledge base create did not return a UUID kb_id.",
      );
      assert(
        fileIds.length === 1 && isUuid(fileIds[0]),
        "Knowledge base create did not return the uploaded file id.",
      );
      cleanup.defer("delete API journey knowledge base", async () => {
        if (!kbId || kbDeleted) return null;
        return client.delete(apiPath("/model-hub/knowledge-base/"), {
          body: { kb_ids: [kbId] },
        });
      });

      const activeAudit = await loadKnowledgeBaseLegacyDbAudit({
        kbId,
        organizationId,
        workspaceId,
      });
      assertKnowledgeBaseLegacyDbAudit(activeAudit, {
        kbId,
        kbName,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedFileIds: fileIds,
      });

      const tablePayload = await client.get(
        apiPath("/model-hub/knowledge-base/get/"),
        {
          query: {
            search: kbName,
            page_number: 0,
            page_size: 10,
            sort: JSON.stringify([{ column_id: "name", type: "ascending" }]),
          },
        },
      );
      const tableRows = payloadArray(tablePayload, "table_data");
      assertKnowledgeBaseTableRows(tableRows, kbId, kbName);

      const optionsPayload = await client.get(
        apiPath("/model-hub/knowledge-base/list/"),
        { query: { search: kbName } },
      );
      const optionsRows = payloadArray(optionsPayload, "table_data");
      assert(
        optionsRows.some((row) => row?.id === kbId && row?.name === kbName),
        "Knowledge base list endpoint did not include the created KB.",
      );

      const filesPayload = await client.post(
        apiPath("/model-hub/knowledge-base/files/"),
        {
          kb_id: kbId,
          search: fileName,
          sort: [{ column_id: "name", type: "ascending" }],
          page_number: 0,
          page_size: 10,
        },
      );
      const fileRows = payloadArray(filesPayload, "table_data");
      assertKnowledgeBaseFileRows(fileRows, fileIds[0], fileName);

      const outsideCandidate = await loadKnowledgeBaseOutsideFileCandidate({
        kbId,
        organizationId,
      });
      const guardedFileId = outsideCandidate?.outside_file_id || randomUUID();
      await expectApiErrorStatus(
        () =>
          client.delete(apiPath("/model-hub/knowledge-base/files/"), {
            body: { kb_id: kbId, file_ids: [guardedFileId] },
          }),
        400,
        "Knowledge base file delete accepted a file outside the selected KB.",
      );
      let guardedFileAudit = null;
      if (outsideCandidate?.outside_file_id) {
        guardedFileAudit = await loadKnowledgeBaseFileDbAudit(guardedFileId);
        assert(
          guardedFileAudit.status === outsideCandidate.outside_file_status,
          "Knowledge base file delete guard mutated a file outside the selected KB.",
        );
      }

      await client.delete(apiPath("/model-hub/knowledge-base/"), {
        body: { kb_ids: [kbId] },
      });
      kbDeleted = true;
      const deletedAudit = await loadKnowledgeBaseLegacyDbAudit({
        kbId,
        organizationId,
        workspaceId,
      });
      assertKnowledgeBaseLegacyDbAudit(deletedAudit, {
        kbId,
        kbName,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedFileIds: fileIds,
      });

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/knowledge-base/files/"), {
            kb_id: kbId,
            page_number: 0,
            page_size: 10,
          }),
        400,
        "Knowledge base files endpoint accepted a deleted KB.",
      );

      evidence.push({
        kb_id: kbId,
        kb_name: kbName,
        file_id: fileIds[0],
        table_rows_seen: tableRows.length,
        files_seen: fileRows.length,
        outside_file_guard_checked: Boolean(outsideCandidate?.outside_file_id),
        outside_file_status_after_guard: guardedFileAudit?.status || null,
        deleted_at_set: deletedAudit.deleted_at_set === true,
      });
    },
  },
  {
    id: "KB-API-003",
    title: "Structured knowledge base viewset lifecycle",
    tags: ["knowledge-base", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      evidence,
      organizationId,
      workspaceId,
      runId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const name = `api_journey_structured_kb_${suffix}`;
      const patchedName = `${name}_patched`;
      const updatedName = `${name}_updated`;
      let kbId = "";
      let deleted = false;

      const hyphenModels = await client.get(
        apiPath("/model-hub/kb/supported-embedding-models"),
      );
      const underscoreModels = await client.get(
        apiPath("/model-hub/kb/supported_embedding_models/"),
      );
      assertStructuredEmbeddingModels(hyphenModels);
      assertStructuredEmbeddingModels(underscoreModels);
      const embeddingModel = hyphenModels[0].value;

      let created;
      try {
        created = await client.post(apiPath("/model-hub/kb/"), {
          name,
          embedding_model: embeddingModel,
          chunk_size: 256,
        });
      } catch (error) {
        if (isLegacyKnowledgeBaseEntitlementDeniedError(error)) {
          evidence.push({
            mode: "structured_create_entitlement_denied",
            status: error.status,
            body: error.body,
          });
          skip(
            "Structured knowledge-base create is entitlement-blocked in this local workspace; KB-API-004 covers the current gate.",
          );
        }
        throw error;
      }
      kbId = created?.id;
      assert(isUuid(kbId), "Structured KB create did not return a UUID id.");
      cleanup.defer(
        "delete API journey structured knowledge base",
        async () => {
          if (!kbId || deleted) return null;
          return ignoreNotFound(() =>
            client.delete(apiPath("/model-hub/kb/{id}/", { id: kbId })),
          );
        },
      );
      assertStructuredKnowledgeBasePayload(created, {
        kbId,
        name,
        embeddingModel,
        chunkSize: 256,
      });

      const activeAudit = await loadStructuredKnowledgeBaseDbAudit({
        kbId,
        organizationId,
        workspaceId,
      });
      assertStructuredKnowledgeBaseDbAudit(activeAudit, {
        kbId,
        name,
        embeddingModel,
        chunkSize: 256,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      const list = await client.get(apiPath("/model-hub/kb/"), {
        query: { search: name, page: 1, page_size: 10 },
      });
      const rows = payloadArray(list, "results");
      assert(
        rows.some((row) => row?.id === kbId && row?.name === name),
        "Structured KB list/search did not return the created KB.",
      );

      const detail = await client.get(
        apiPath("/model-hub/kb/{id}/", { id: kbId }),
      );
      assertStructuredKnowledgeBasePayload(detail, {
        kbId,
        name,
        embeddingModel,
        chunkSize: 256,
      });

      const patched = await client.patch(
        apiPath("/model-hub/kb/{id}/", { id: kbId }),
        { name: patchedName },
      );
      assertStructuredKnowledgeBasePayload(patched, {
        kbId,
        name: patchedName,
        embeddingModel,
        chunkSize: 256,
      });

      const updated = await client.put(
        apiPath("/model-hub/kb/{id}/", { id: kbId }),
        {
          name: updatedName,
          embedding_model: embeddingModel,
          chunk_size: 512,
        },
      );
      assertStructuredKnowledgeBasePayload(updated, {
        kbId,
        name: updatedName,
        embeddingModel,
        chunkSize: 512,
      });

      await expectApiErrorStatus(
        () => client.get(apiPath("/model-hub/kb/{id}/", { id: randomUUID() })),
        404,
        "Structured KB missing detail returned a non-404 response.",
      );

      await client.delete(apiPath("/model-hub/kb/{id}/", { id: kbId }));
      deleted = true;
      const deletedAudit = await loadStructuredKnowledgeBaseDbAudit({
        kbId,
        organizationId,
        workspaceId,
      });
      assertStructuredKnowledgeBaseDbAudit(deletedAudit, {
        kbId,
        name: updatedName,
        embeddingModel,
        chunkSize: 512,
        organizationId,
        workspaceId,
        expectedDeleted: true,
      });

      await expectApiErrorStatus(
        () => client.get(apiPath("/model-hub/kb/{id}/", { id: kbId })),
        404,
        "Structured KB detail still returned after delete.",
      );

      evidence.push({
        kb_id: kbId,
        created_name: name,
        updated_name: updatedName,
        embedding_model: embeddingModel,
        supported_model_count: hyphenModels.length,
        list_rows_seen: rows.length,
        deleted_at_set: deletedAudit.deleted_at_set === true,
        frontend_usage:
          "No current frontend caller found; UI uses legacy endpoints.knowledge.*",
      });
    },
  },
  {
    id: "KB-API-004",
    title: "Legacy knowledge base entitlement gate and structured KB read availability",
    tags: ["knowledge-base", "safe", "entitlement", "smoke"],
    async run({ client, evidence }) {
      const tableError = await expectLegacyKnowledgeBaseEntitlementDenied(
        () =>
          client.get(apiPath("/model-hub/knowledge-base/get/"), {
            query: { page_number: 0, page_size: 1 },
          }),
        "Legacy Knowledge Base table endpoint did not return the expected entitlement gate.",
      );
      const optionError = await expectLegacyKnowledgeBaseEntitlementDenied(
        () => client.get(apiPath("/model-hub/knowledge-base/list/")),
        "Legacy Knowledge Base option endpoint did not return the expected entitlement gate.",
      );

      const structuredList = await client.get(apiPath("/model-hub/kb/"), {
        query: { page: 1, page_size: 5 },
      });
      const structuredRows = payloadArray(structuredList, "results");
      const embeddingModels = asArray(
        await client.get(apiPath("/model-hub/kb/supported-embedding-models")),
      );
      assertStructuredEmbeddingModels(embeddingModels);
      const structuredCreateError =
        await expectLegacyKnowledgeBaseEntitlementDenied(
          () =>
            client.post(apiPath("/model-hub/kb/"), {
              name: `api_journey_structured_gate_${Date.now().toString(36)}`,
              embedding_model: embeddingModels[0].value,
              chunk_size: 256,
            }),
          "Structured Knowledge Base create did not return the expected entitlement gate.",
        );

      evidence.push({
        legacy_table_status: tableError.status,
        legacy_table_code: tableError.body?.code,
        legacy_option_status: optionError.status,
        legacy_option_code: optionError.body?.code,
        structured_create_status: structuredCreateError.status,
        structured_create_code: structuredCreateError.body?.code,
        structured_kb_count: structuredList?.count ?? structuredRows.length,
        structured_embedding_model_count: embeddingModels.length,
        mode: "legacy_and_structured_create_entitlement_denied_structured_read_available",
      });
    },
  },
  {
    id: "EVAL-API-001",
    title: "Eval template list/detail read path",
    tags: ["evals", "safe", "smoke"],
    async run({ client, evidence }) {
      const templates = asArray(
        await client.post(apiPath("/model-hub/eval-templates/list/"), {
          page: 0,
          page_size: 10,
          owner_filter: "all",
          sort_by: "updated_at",
          sort_order: "desc",
        }),
      );
      const template = templates.find((row) => row?.id);
      if (!template)
        skip("No eval templates found for this account/workspace.");

      const detail = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/detail/", {
          template_id: template.id,
        }),
      );
      const detailId =
        detail?.id || detail?.template?.id || detail?.eval_template?.id;
      assert(
        detailId === template.id,
        "Eval template detail returned wrong id.",
      );
      evidence.push({ eval_template_id: template.id, name: detail.name });
    },
  },
  {
    id: "EVAL-API-002",
    title:
      "Eval template create, update, detail reload, version list, and delete lifecycle",
    tags: ["evals", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const name = `api_journey_eval_${suffix}`;
      const updatedName = `${name}_updated`;

      const created = await client.post(
        apiPath("/model-hub/eval-templates/create-v2/"),
        {
          name,
          eval_type: "code",
          code: "def evaluate(output, expected=None):\n    return True",
          code_language: "python",
          output_type: "pass_fail",
          description: "Created by API journey regression.",
          tags: ["api-journey"],
        },
      );
      const templateId = created?.id;
      assert(templateId, "Eval template create did not return id.");
      cleanup.defer("delete API journey eval template", () =>
        client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        }),
      );

      const detail = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/detail/", {
          template_id: templateId,
        }),
      );
      assert(
        detail?.name === name,
        "Created eval template detail returned wrong name.",
      );
      assert(
        detail?.output_type === "pass_fail",
        "Created eval template detail returned wrong output_type.",
      );

      await client.put(
        apiPath("/model-hub/eval-templates/{template_id}/update/", {
          template_id: templateId,
        }),
        {
          name: updatedName,
          output_type: "percentage",
          pass_threshold: 0.75,
          description: "Updated by API journey regression.",
          tags: ["api-journey", "updated"],
        },
      );

      const updated = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/detail/", {
          template_id: templateId,
        }),
      );
      assert(
        updated?.name === updatedName,
        "Eval template update did not persist name.",
      );
      assert(
        updated?.output_type === "percentage",
        "Eval template update did not persist output_type.",
      );
      assert(
        Number(updated?.pass_threshold) === 0.75,
        "Eval template update did not persist pass_threshold.",
      );

      const versions = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/versions/", {
          template_id: templateId,
        }),
      );
      assert(
        asArray(versions?.versions || versions).length >= 1,
        "Eval template versions endpoint did not return the initial version.",
      );

      evidence.push({
        eval_template_id: templateId,
        updated_name: updatedName,
      });
    },
  },
  {
    id: "EVAL-API-003",
    title: "Eval template versions, usage, metrics, and ground-truth lifecycle",
    tags: ["evals", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_eval_gt_${suffix}`;
      const groundTruthName = `api_journey_gt_${suffix}`;

      const created = await client.post(
        apiPath("/model-hub/eval-templates/create-v2/"),
        {
          name: templateName,
          eval_type: "code",
          code: "def evaluate(output=None, expected=None):\n    return True",
          code_language: "python",
          output_type: "pass_fail",
          description: "Created by API journey ground-truth regression.",
          tags: ["api-journey", "ground-truth"],
          instructions: "Return pass/fail for the provided output.",
        },
      );
      const templateId = created?.id;
      assert(
        isUuid(templateId),
        "Eval template create did not return a UUID id.",
      );
      cleanup.defer("delete API journey eval template", () =>
        client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        }),
      );

      const listPayload = await client.post(
        apiPath("/model-hub/eval-templates/list/"),
        {
          page: 0,
          page_size: 10,
          owner_filter: "user",
          search: templateName,
          sort_by: "updated_at",
          sort_order: "desc",
        },
      );
      const listItems = payloadArray(listPayload?.items, "items");
      assert(
        listItems.some((item) => item.id === templateId),
        "Eval template list did not include the newly created template.",
      );

      const [detail, usage, feedbackList, charts, metricGet, metricPost] =
        await Promise.all([
          client.get(
            apiPath("/model-hub/eval-templates/{template_id}/detail/", {
              template_id: templateId,
            }),
          ),
          client.get(
            apiPath("/model-hub/eval-templates/{template_id}/usage/", {
              template_id: templateId,
            }),
            { query: { page: 0, page_size: 5, period: "30d" } },
          ),
          client.get(
            apiPath("/model-hub/eval-templates/{template_id}/feedback-list/", {
              template_id: templateId,
            }),
            { query: { page: 0, page_size: 5 } },
          ),
          client.post(apiPath("/model-hub/eval-templates/list-charts/"), {
            template_ids: [templateId],
          }),
          client.get(apiPath("/model-hub/get-eval-metrics"), {
            query: { eval_template_id: templateId },
          }),
          client.post(apiPath("/model-hub/get-eval-metrics"), {
            eval_template_id: templateId,
            filters: [],
          }),
        ]);
      assertEvalTemplateDetail(detail, templateId, templateName);
      assertEvalUsagePayload(usage, templateId);
      assertEvalFeedbackListPayload(feedbackList, templateId);
      assertEvalChartsPayload(charts, templateId);
      assertEvalMetricPayload(metricGet, templateId);
      assertEvalMetricPayload(metricPost, templateId);

      const initialVersions = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/versions/", {
          template_id: templateId,
        }),
      );
      assertEvalVersionsPayload(initialVersions, templateId, 1);

      const version = await client.post(
        apiPath("/model-hub/eval-templates/{template_id}/versions/create/", {
          template_id: templateId,
        }),
        {
          criteria: "API journey version two criteria.",
          model: "turing_large",
          config_snapshot: {
            code: "def evaluate(output=None, expected=None):\n    return True",
            language: "python",
            output: "Pass/Fail",
          },
        },
      );
      assert(
        isUuid(version?.id),
        "Eval version create did not return a UUID id.",
      );
      assert(
        Number(version.version_number) === 2,
        "Eval version create did not create version 2.",
      );

      const setDefault = await client.put(
        apiPath(
          "/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/",
          { template_id: templateId, version_id: version.id },
        ),
        {},
      );
      assert(
        setDefault?.id === version.id && setDefault.is_default === true,
        "Eval version set-default did not mark version 2 default.",
      );

      const restored = await client.post(
        apiPath(
          "/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/",
          { template_id: templateId, version_id: version.id },
        ),
        {},
      );
      assert(
        isUuid(restored?.id),
        "Eval version restore did not return a UUID id.",
      );
      assert(
        Number(restored.restored_from) === 2,
        "Eval version restore did not report the restored source version.",
      );

      const versionsAfterRestore = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/versions/", {
          template_id: templateId,
        }),
      );
      assertEvalVersionsPayload(versionsAfterRestore, templateId, 3);

      const emptyGroundTruthConfig = await client.get(
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth-config/",
          { template_id: templateId },
        ),
      );
      assert(
        emptyGroundTruthConfig?.ground_truth?.enabled === false,
        "New eval template did not start with ground truth disabled.",
      );

      const emptyGroundTruthList = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/ground-truth/", {
          template_id: templateId,
        }),
      );
      assert(
        emptyGroundTruthList?.template_id === templateId &&
          emptyGroundTruthList.total === 0,
        "New eval template ground-truth list was not empty.",
      );

      const groundTruth = await client.post(
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
          { template_id: templateId },
        ),
        {
          name: groundTruthName,
          description: "Ground truth rows created by API journey.",
          file_name: "api-journey-ground-truth.json",
          columns: ["question", "answer"],
          data: [
            { question: "q1", answer: "a1" },
            { question: "q2", answer: "a2" },
          ],
          variable_mapping: { question: "input" },
          role_mapping: { expected_output: "answer" },
        },
      );
      const groundTruthId = groundTruth?.id;
      assert(
        isUuid(groundTruthId),
        "Ground-truth upload did not return a UUID id.",
      );
      cleanup.defer("delete API journey ground truth", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/ground-truth/{ground_truth_id}/", {
              ground_truth_id: groundTruthId,
            }),
          ),
        ),
      );

      const groundTruthConfig = await client.put(
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth-config/",
          { template_id: templateId },
        ),
        {
          enabled: true,
          ground_truth_id: groundTruthId,
          mode: "manual",
          max_examples: 2,
          similarity_threshold: 0.4,
          injection_format: "structured",
        },
      );
      assert(
        groundTruthConfig?.ground_truth?.ground_truth_id === groundTruthId,
        "Ground-truth config did not persist selected ground truth id.",
      );

      const [
        groundTruthList,
        groundTruthData,
        groundTruthStatus,
        groundTruthMapping,
        groundTruthRoleMapping,
      ] = await Promise.all([
        client.get(
          apiPath("/model-hub/eval-templates/{template_id}/ground-truth/", {
            template_id: templateId,
          }),
        ),
        client.get(
          apiPath("/model-hub/ground-truth/{ground_truth_id}/data/", {
            ground_truth_id: groundTruthId,
          }),
          { query: { page: 0, page_size: 5 } },
        ),
        client.get(
          apiPath("/model-hub/ground-truth/{ground_truth_id}/status/", {
            ground_truth_id: groundTruthId,
          }),
        ),
        client.put(
          apiPath("/model-hub/ground-truth/{ground_truth_id}/mapping/", {
            ground_truth_id: groundTruthId,
          }),
          { variable_mapping: { question: "prompt", answer: "reference" } },
        ),
        client.put(
          apiPath("/model-hub/ground-truth/{ground_truth_id}/role-mapping/", {
            ground_truth_id: groundTruthId,
          }),
          { role_mapping: { input: "question", expected_output: "answer" } },
        ),
      ]);
      assertGroundTruthListPayload(groundTruthList, templateId, groundTruthId);
      assertGroundTruthDataPayload(groundTruthData, groundTruthId);
      assertGroundTruthStatusPayload(groundTruthStatus, groundTruthId);
      assert(
        groundTruthMapping?.variable_mapping?.question === "prompt",
        "Ground-truth variable mapping did not persist.",
      );
      assert(
        groundTruthRoleMapping?.role_mapping?.expected_output === "answer",
        "Ground-truth role mapping did not persist.",
      );

      const templateAudit = await loadEvalTemplateLifecycleDbAudit(
        templateId,
        organizationId,
        workspaceId,
      );
      assertEvalTemplateLifecycleDbAudit(templateAudit, {
        templateId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedVersions: 3,
        groundTruthId,
      });
      const groundTruthAudit = await loadGroundTruthDbAudit(
        groundTruthId,
        templateId,
        organizationId,
        workspaceId,
      );
      assertGroundTruthDbAudit(groundTruthAudit, {
        groundTruthId,
        templateId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      const deletedGroundTruth = await client.delete(
        apiPath("/model-hub/ground-truth/{ground_truth_id}/", {
          ground_truth_id: groundTruthId,
        }),
      );
      assert(
        deletedGroundTruth?.deleted === true,
        "Ground-truth delete did not return deleted=true.",
      );
      const deletedTemplate = await client.post(
        apiPath("/model-hub/eval-templates/bulk-delete/"),
        { template_ids: [templateId] },
      );
      assert(
        Number(deletedTemplate?.deleted_count) === 1,
        "Eval template bulk delete did not delete the template.",
      );

      const postDeleteTemplateAudit = await loadEvalTemplateLifecycleDbAudit(
        templateId,
        organizationId,
        workspaceId,
      );
      assertEvalTemplateLifecycleDbAudit(postDeleteTemplateAudit, {
        templateId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedVersions: 3,
        groundTruthId,
      });
      const postDeleteGroundTruthAudit = await loadGroundTruthDbAudit(
        groundTruthId,
        templateId,
        organizationId,
        workspaceId,
      );
      assertGroundTruthDbAudit(postDeleteGroundTruthAudit, {
        groundTruthId,
        templateId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
      });

      evidence.push({
        eval_template_id: templateId,
        ground_truth_id: groundTruthId,
        versions: Number(postDeleteTemplateAudit.version_count),
        max_version: Number(postDeleteTemplateAudit.max_version_number),
        ground_truth_rows: Number(postDeleteGroundTruthAudit.row_count),
        template_deleted_at_set: postDeleteTemplateAudit.deleted_at_set,
        ground_truth_deleted_at_set: postDeleteGroundTruthAudit.deleted_at_set,
      });
    },
  },
  {
    id: "EVAL-API-004",
    title: "Eval playground SDK snippets use placeholder credentials",
    tags: ["evals", "safe", "credential-safety", "db-audit"],
    async run({ client, evidence, user, organizationId }) {
      const email = currentUserEmail(user);
      if (!email) skip("Current user email was not available for DB audit.");

      const templates = asArray(
        await client.post(apiPath("/model-hub/eval-templates/list/"), {
          page: 0,
          page_size: 10,
          owner_filter: "all",
          sort_by: "updated_at",
          sort_order: "desc",
        }),
      );
      const template = templates.find((row) => row?.id);
      if (!template)
        skip("No eval templates found for eval SDK snippet coverage.");

      const beforeAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );
      const result = await client.get(apiPath("/model-hub/eval-sdk-code/"), {
        query: {
          template_id: template.id,
          model: "gpt-4o-mini",
          mapping: JSON.stringify({ response: "api journey" }),
        },
      });
      const afterAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );

      assertEvalSdkSnippetSafety(result);
      assert(
        Number(afterAudit.user_key_count) ===
          Number(beforeAudit.user_key_count),
        "eval SDK code generation created or deleted a user API key.",
      );

      evidence.push({
        eval_template_id: template.id,
        code_keys: Object.keys(result || {}).sort(),
        code_lengths: Object.fromEntries(
          Object.entries(result || {})
            .filter(([, value]) => typeof value === "string")
            .map(([key, value]) => [key, value.length]),
        ),
        user_key_count_before: Number(beforeAudit.user_key_count),
        user_key_count_after: Number(afterAudit.user_key_count),
        placeholders_present: true,
      });
    },
  },
  {
    id: "EVAL-API-018",
    title: "Legacy custom eval create contract and cleanup",
    tags: ["evals", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_custom_eval_${suffix}`;
      const createPayload = {
        name: templateName,
        description: "Custom eval created by API journey.",
        template_type: "Futureagi",
        output_type: "Pass/Fail",
        required_keys: ["response"],
        criteria: "Judge {{response}} for clarity.",
        tags: ["api-journey", "custom-eval"],
        config: {
          model: "turing_large",
          proxy_agi: true,
          visible_ui: true,
        },
        check_internet: false,
      };

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/create_custom_evals/"), {
            ...createPayload,
            name: `${templateName}_unknown_field`,
            requiredKeys: ["legacy"],
          }),
        400,
        "create_custom_evals accepted an unknown camelCase request field.",
      );

      const created = await client.post(
        apiPath("/model-hub/create_custom_evals/"),
        createPayload,
      );
      const createResult = created?.result || created?.data?.result || created;
      const templateId = createResult?.eval_template_id;
      assert(
        isUuid(templateId),
        "create_custom_evals did not return eval_template_id.",
      );

      let templateDeleted = false;
      cleanup.defer("delete API journey legacy custom eval", () => {
        if (templateDeleted) return null;
        return client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        });
      });

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/create_custom_evals/"),
            createPayload,
          ),
        400,
        "create_custom_evals accepted a duplicate active template name.",
      );

      const audit = await loadCustomEvalCreateDbAudit({
        templateId,
        organizationId,
        workspaceId,
      });
      assertCustomEvalCreateDbAudit(audit, {
        templateId,
        templateName,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      const deleted = await client.post(
        apiPath("/model-hub/eval-templates/bulk-delete/"),
        { template_ids: [templateId] },
      );
      assert(
        Number(deleted?.deleted_count) === 1,
        "Custom eval cleanup did not delete the created template.",
      );
      templateDeleted = true;

      const deletedAudit = await loadCustomEvalCreateDbAudit({
        templateId,
        organizationId,
        workspaceId,
      });
      assertCustomEvalCreateDbAudit(deletedAudit, {
        templateId,
        templateName,
        organizationId,
        workspaceId,
        expectedDeleted: true,
      });

      evidence.push({
        eval_template_id: templateId,
        eval_type_id: audit.eval_type_id,
        required_keys: audit.required_keys,
        version_count: Number(audit.version_count),
        default_version_count: Number(audit.default_version_count),
        deleted_at_set: deletedAudit.deleted_at_set,
      });
    },
  },
  {
    id: "EVAL-API-019",
    title: "Legacy eval-template create contract and version audit",
    tags: ["evals", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .slice(0, 22);
      const templateName = `api_legacy_eval_${suffix}`;
      const systemOwnerName = `api_legacy_eval_sys_${suffix}`;
      const createPayload = {
        name: templateName,
        config: {
          required_keys: ["response"],
          eval_type_id: "OutputEvaluator",
          output: "Pass/Fail",
        },
        eval_tags: ["api-journey", "legacy-eval-template"],
      };

      cleanup.defer(
        "hard delete API journey legacy eval-template create rows",
        () =>
          hardDeleteLegacyEvalTemplateCreateFixture(
            [templateName, systemOwnerName],
            organizationId,
          ),
      );

      const created = await client.post(
        apiPath("/model-hub/eval-template/create/"),
        createPayload,
      );
      assert(
        created === "success" || created?.result === "success",
        "Legacy eval-template create did not return success.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/eval-template/create/"),
            createPayload,
          ),
        400,
        "Legacy eval-template create accepted a duplicate active template name.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/eval-template/create/"), {
            ...createPayload,
            name: systemOwnerName,
            owner: "system",
          }),
        400,
        "Legacy eval-template create allowed a user to create owner=system.",
      );

      const audit = await loadLegacyEvalTemplateCreateAudit({
        templateName,
        systemOwnerName,
        organizationId,
        workspaceId,
      });
      assert(
        Number(audit.active_template_count) === 1,
        "Legacy eval-template create did not leave exactly one active template.",
      );
      assert(
        isUuid(audit.template_id),
        "Legacy eval-template audit did not find the created template id.",
      );
      assert(
        audit.owner === "user",
        "Legacy eval-template create did not force owner=user.",
      );
      assert(
        !workspaceId || audit.workspace_id === workspaceId,
        "Legacy eval-template create did not persist request workspace.",
      );
      assert(
        Number(audit.version_count) === 1 &&
          Number(audit.default_version_count) === 1,
        "Legacy eval-template create did not create exactly one default version.",
      );
      assert(
        Number(audit.version_scope_mismatch_count) === 0,
        "Legacy eval-template version scope did not match the template.",
      );
      assert(
        Number(audit.system_owner_count) === 0,
        "Legacy eval-template create inserted owner=system rows.",
      );
      assert(
        asArray(audit.required_keys).includes("response"),
        "Legacy eval-template config did not persist required_keys.",
      );

      evidence.push({
        eval_template_id: audit.template_id,
        template_name: templateName,
        owner: audit.owner,
        workspace_id: audit.workspace_id,
        version_count: Number(audit.version_count),
        default_version_count: Number(audit.default_version_count),
        duplicate_guard: "passed",
        system_owner_guard: "passed",
      });
    },
  },
  {
    id: "EVAL-API-020",
    title: "Legacy eval-user-template create and evaluate-rows scoping",
    tags: ["evals", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .slice(0, 22);
      const metricName = `api_legacy_user_eval_${suffix}`;
      const duplicateName = `api_legacy_user_dup_${suffix}`;
      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip(
          "System eval word_count_in_range was not available for legacy eval-user-template coverage.",
        );
      }
      const requiredKeys = promptEvalRequiredKeys(template);
      if (!requiredKeys.includes("text")) {
        skip("word_count_in_range did not expose the expected text input key.");
      }
      const paramsConfig = promptEvalParamsForTemplate(template);
      const fixture = await seedLegacyEvalUserTemplateFixture({
        runId,
        organizationId,
        workspaceId,
        templateId: template.id,
      });
      assert(
        fixture?.fixture_created,
        "Legacy eval-user-template fixture seed did not create datasets.",
      );
      cleanup.defer(
        "hard delete API journey legacy eval-user-template fixture",
        () => hardDeleteLegacyEvalUserTemplateFixture(fixture),
      );

      const createPayload = {
        name: metricName,
        template_id: template.id,
        dataset_id: fixture.dataset_id,
        config: {
          mapping: { text: fixture.input_column_id },
          params: paramsConfig.params,
        },
        model: "turing_small",
      };

      const created = await client.post(
        apiPath("/model-hub/eval-user-template/create/"),
        createPayload,
      );
      assert(
        created === "success" || created?.result === "success",
        "Legacy eval-user-template create did not return success.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/eval-user-template/create/"),
            createPayload,
          ),
        400,
        "Legacy eval-user-template create accepted a duplicate metric name.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/eval-user-template/create/"), {
            ...createPayload,
            name: duplicateName,
            config: {
              ...createPayload.config,
              mapping: { text: fixture.outside_column_id },
            },
          }),
        400,
        "Legacy eval-user-template create accepted a mapped column outside the selected dataset.",
      );

      let otherWorkspaceCreateGuard = "skipped:no-other-workspace";
      if (fixture.other_dataset_id) {
        await expectApiErrorStatus(
          () =>
            client.post(apiPath("/model-hub/eval-user-template/create/"), {
              ...createPayload,
              name: `${duplicateName}_other`,
              dataset_id: fixture.other_dataset_id,
              config: {
                ...createPayload.config,
                mapping: { text: fixture.other_column_id },
              },
            }),
          404,
          "Legacy eval-user-template create accepted a same-org dataset from another workspace.",
        );
        otherWorkspaceCreateGuard = "passed";
      }

      const createAudit = await loadLegacyEvalUserTemplateAudit({
        fixture,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertLegacyEvalUserTemplateCreateAudit(createAudit, {
        fixture,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
        expectedParams: paramsConfig.params,
      });
      const metricId = createAudit.metric_id;
      const seededCells = await seedLegacyEvalUserTemplateEvaluationCells({
        fixture,
        metricId,
      });
      assert(
        seededCells?.inserted_eval_cell_count === 3,
        "Legacy evaluate-rows fixture did not seed scoped and guard eval cells.",
      );

      const beforeEvaluateAudit = await loadLegacyEvalUserTemplateAudit({
        fixture,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertLegacyEvalCellState(beforeEvaluateAudit, fixture.row_one_id, {
        status: "pass",
        value: fixture.eval_value_one,
      });
      assertLegacyEvalCellState(beforeEvaluateAudit, fixture.row_two_id, {
        status: "pass",
        value: fixture.eval_value_two,
      });

      const singleRowQueued = await client.post(
        apiPath("/model-hub/evaluate-rows/"),
        {
          row_ids: [fixture.row_one_id],
          user_eval_metric_ids: [metricId],
        },
      );
      assert(
        singleRowQueued?.success ||
          singleRowQueued?.result?.success ||
          JSON.stringify(singleRowQueued || {}).includes("Evaluations queued"),
        "evaluate-rows did not acknowledge the explicit row queue request.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/evaluate-rows/"), {
            row_ids: [fixture.outside_row_id],
            user_eval_metric_ids: [metricId],
          }),
        404,
        "evaluate-rows accepted a row outside the metric dataset.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/evaluate-rows/"), {
            selected_all_rows: true,
            row_ids: [fixture.outside_row_id],
            user_eval_metric_ids: [metricId],
          }),
        404,
        "evaluate-rows accepted a selected-all exclusion row outside the metric dataset.",
      );

      let otherWorkspaceMetricGuard = "skipped:no-other-workspace";
      if (fixture.other_metric_id) {
        await expectApiErrorStatus(
          () =>
            client.post(apiPath("/model-hub/evaluate-rows/"), {
              row_ids: [fixture.other_row_id],
              user_eval_metric_ids: [fixture.other_metric_id],
            }),
          404,
          "evaluate-rows accepted a metric from another workspace.",
        );
        otherWorkspaceMetricGuard = "passed";
      }

      const singleRowAudit = await loadLegacyEvalUserTemplateAudit({
        fixture,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertLegacyEvalCellState(singleRowAudit, fixture.row_one_id, {
        status: "running",
        value: null,
        valueInfos: {},
      });
      assertLegacyEvalCellState(singleRowAudit, fixture.row_two_id, {
        status: "pass",
        value: fixture.eval_value_two,
      });
      assert(
        singleRowAudit.outside_eval_cell_status === "pass" &&
          singleRowAudit.outside_eval_cell_value === fixture.outside_eval_value,
        "evaluate-rows outside-row guard mutated the outside dataset eval cell.",
      );
      if (fixture.other_metric_id) {
        assert(
          singleRowAudit.other_eval_cell_status === "pass" &&
            singleRowAudit.other_eval_cell_value === fixture.other_eval_value,
          "evaluate-rows other-workspace guard mutated the other workspace eval cell.",
        );
      }

      await client.post(apiPath("/model-hub/evaluate-rows/"), {
        selected_all_rows: true,
        row_ids: [fixture.row_one_id],
        user_eval_metric_ids: [metricId],
      });

      const selectedAllAudit = await loadLegacyEvalUserTemplateAudit({
        fixture,
        templateId: template.id,
        metricName,
        organizationId,
        workspaceId,
      });
      assertLegacyEvalCellState(selectedAllAudit, fixture.row_one_id, {
        status: "running",
        value: null,
        valueInfos: {},
      });
      assertLegacyEvalCellState(selectedAllAudit, fixture.row_two_id, {
        status: "running",
        value: null,
        valueInfos: {},
      });
      assert(
        Number(selectedAllAudit.running_eval_cell_count) === 2,
        "evaluate-rows selected_all_rows did not reset the remaining scoped row.",
      );

      evidence.push({
        dataset_id: fixture.dataset_id,
        eval_template_id: template.id,
        user_eval_metric_id: metricId,
        metric_name: metricName,
        duplicate_guard: "passed",
        mapping_guard: "passed",
        row_scope_guard: "passed",
        selected_all_scope_guard: "passed",
        other_workspace_create_guard: otherWorkspaceCreateGuard,
        other_workspace_metric_guard: otherWorkspaceMetricGuard,
        running_eval_cell_count: Number(
          selectedAllAudit.running_eval_cell_count,
        ),
      });
    },
  },
  {
    id: "EVAL-API-021",
    title: "Ground-truth embed/search guards and workspace scope",
    tags: ["evals", "mutating", "provider-guard", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .slice(0, 22);
      const templateName = `api_gt_retrieval_${suffix}`;
      const emptyGroundTruthName = `api_gt_retrieval_empty_${suffix}`;
      const groundTruthName = `api_gt_retrieval_rows_${suffix}`;

      const created = await client.post(
        apiPath("/model-hub/eval-templates/create-v2/"),
        {
          name: templateName,
          eval_type: "code",
          code: "def evaluate(output=None, expected=None):\n    return True",
          code_language: "python",
          output_type: "pass_fail",
          description: "Created by API journey ground-truth retrieval guards.",
          tags: ["api-journey", "ground-truth-retrieval"],
        },
      );
      const templateId = created?.id;
      assert(
        isUuid(templateId),
        "Ground-truth retrieval eval template create did not return a UUID id.",
      );
      cleanup.defer(
        "hard delete API journey ground-truth retrieval fixture",
        () =>
          hardDeleteGroundTruthRetrievalFixture([templateId], organizationId),
      );

      const emptyGroundTruth = await client.post(
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
          { template_id: templateId },
        ),
        {
          name: emptyGroundTruthName,
          description: "Empty ground truth for embed guard coverage.",
          file_name: "api-journey-empty-ground-truth.json",
          columns: ["question"],
          data: [],
        },
      );
      const emptyGroundTruthId = emptyGroundTruth?.id;
      assert(
        isUuid(emptyGroundTruthId),
        "Empty ground-truth upload did not return a UUID id.",
      );

      const groundTruth = await client.post(
        apiPath(
          "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
          { template_id: templateId },
        ),
        {
          name: groundTruthName,
          description: "Ground truth rows for retrieval guard coverage.",
          file_name: "api-journey-ground-truth-retrieval.json",
          columns: ["question", "answer"],
          data: [
            { question: "What is 1+1?", answer: "2" },
            { question: "Capital of France?", answer: "Paris" },
          ],
          role_mapping: { input: "question", expected_output: "answer" },
        },
      );
      const groundTruthId = groundTruth?.id;
      assert(
        isUuid(groundTruthId),
        "Ground-truth upload did not return a UUID id.",
      );

      const pendingSearchError = await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/ground-truth/{ground_truth_id}/search/", {
              ground_truth_id: groundTruthId,
            }),
            { query: "What is 1+1?", max_results: 2 },
          ),
        400,
        "Ground-truth search accepted a pending embedding dataset.",
      );
      assert(
        String(pendingSearchError.body?.message || "").includes(
          "Embeddings not ready",
        ),
        "Pending ground-truth search did not return the embeddings-not-ready message.",
      );

      const emptyEmbedError = await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/ground-truth/{ground_truth_id}/embed/", {
              ground_truth_id: emptyGroundTruthId,
            }),
            {},
          ),
        400,
        "Ground-truth embed accepted an empty dataset.",
      );
      assert(
        emptyEmbedError.body?.message === "No data rows to embed.",
        "Empty ground-truth embed returned an unexpected error message.",
      );

      await setGroundTruthEmbeddingStatus({
        groundTruthId,
        embeddingStatus: "processing",
        embeddedRowCount: 1,
      });

      const processingEmbedError = await expectApiErrorStatus(
        () =>
          client.post(
            apiPath("/model-hub/ground-truth/{ground_truth_id}/embed/", {
              ground_truth_id: groundTruthId,
            }),
            {},
          ),
        400,
        "Ground-truth embed accepted a dataset already marked processing.",
      );
      assert(
        processingEmbedError.body?.message ===
          "Embedding generation is already in progress.",
        "Processing ground-truth embed returned an unexpected error message.",
      );

      const groundTruthIds = [emptyGroundTruthId, groundTruthId];
      const otherWorkspaceId = await findOtherWorkspaceId(
        organizationId,
        workspaceId,
      );
      let otherWorkspaceGuard = "skipped:no-other-workspace";
      if (otherWorkspaceId) {
        const otherTemplateName = `api_gt_retrieval_other_${suffix}`;
        const otherCreated = await client.post(
          apiPath("/model-hub/eval-templates/create-v2/"),
          {
            name: otherTemplateName,
            eval_type: "code",
            code: "def evaluate(output=None, expected=None):\n    return True",
            code_language: "python",
            output_type: "pass_fail",
            description:
              "Created by API journey ground-truth retrieval workspace guard.",
            tags: ["api-journey", "ground-truth-retrieval"],
          },
        );
        const otherTemplateId = otherCreated?.id;
        assert(
          isUuid(otherTemplateId),
          "Other-workspace eval template create did not return a UUID id.",
        );
        cleanup.defer(
          "hard delete API journey ground-truth retrieval other-workspace fixture",
          () =>
            hardDeleteGroundTruthRetrievalFixture(
              [otherTemplateId],
              organizationId,
            ),
        );

        const otherGroundTruth = await client.post(
          apiPath(
            "/model-hub/eval-templates/{template_id}/ground-truth/upload/",
            { template_id: otherTemplateId },
          ),
          {
            name: `api_gt_retrieval_other_rows_${suffix}`,
            description: "Other workspace ground truth guard fixture.",
            file_name: "api-journey-ground-truth-other-workspace.json",
            columns: ["question", "answer"],
            data: [{ question: "private", answer: "hidden" }],
            role_mapping: { input: "question", expected_output: "answer" },
          },
        );
        const otherGroundTruthId = otherGroundTruth?.id;
        assert(
          isUuid(otherGroundTruthId),
          "Other-workspace ground-truth upload did not return a UUID id.",
        );
        await moveGroundTruthRetrievalFixtureToWorkspace({
          templateId: otherTemplateId,
          groundTruthId: otherGroundTruthId,
          workspaceId: otherWorkspaceId,
        });
        groundTruthIds.push(otherGroundTruthId);

        await expectApiErrorStatus(
          () =>
            client.post(
              apiPath("/model-hub/ground-truth/{ground_truth_id}/search/", {
                ground_truth_id: otherGroundTruthId,
              }),
              { query: "private", max_results: 1 },
            ),
          404,
          "Ground-truth search accepted a same-org other-workspace dataset.",
        );
        await expectApiErrorStatus(
          () =>
            client.post(
              apiPath("/model-hub/ground-truth/{ground_truth_id}/embed/", {
                ground_truth_id: otherGroundTruthId,
              }),
              {},
            ),
          404,
          "Ground-truth embed accepted a same-org other-workspace dataset.",
        );
        otherWorkspaceGuard = "passed";
      }

      const audit = await loadGroundTruthRetrievalAudit({
        groundTruthIds,
        organizationId,
      });
      const primaryAudit = findGroundTruthAudit(audit, groundTruthId);
      const emptyAudit = findGroundTruthAudit(audit, emptyGroundTruthId);
      assert(
        primaryAudit.workspace_id === workspaceId,
        "Ground-truth retrieval audit primary workspace mismatch.",
      );
      assert(
        primaryAudit.embedding_status === "processing" &&
          Number(primaryAudit.embedded_row_count) === 1,
        "Processing embed guard mutated the primary ground-truth status.",
      );
      assert(
        emptyAudit.row_count === 0 && emptyAudit.embedding_status === "pending",
        "Empty embed guard mutated the empty ground-truth status.",
      );
      assert(
        Number(audit.embedding_count) === 0,
        "Ground-truth retrieval guard journey unexpectedly created embeddings.",
      );

      evidence.push({
        eval_template_id: templateId,
        ground_truth_id: groundTruthId,
        empty_ground_truth_id: emptyGroundTruthId,
        pending_search_guard: "passed",
        empty_embed_guard: "passed",
        processing_embed_guard: "passed",
        other_workspace_guard: otherWorkspaceGuard,
        embedding_count: Number(audit.embedding_count),
      });
    },
  },
  {
    id: "EVAL-API-005",
    title: "Eval summary template CRUD round trip",
    tags: ["evals", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const name = `api journey summary ${suffix}`;
      const description = `Summary template created by ${runId}`;
      const criteria = `Group failures by root cause for ${runId}.`;
      const updatedName = `${name} updated`;
      const updatedDescription = `${description} updated`;
      const updatedCriteria = `${criteria} Include severity buckets.`;

      const beforeList = await client.get(
        apiPath("/model-hub/eval-summary-templates/"),
      );
      assertEvalSummaryTemplateList(beforeList);

      const created = await client.post(
        apiPath("/model-hub/eval-summary-templates/"),
        { name, description, criteria },
      );
      const templateId = created?.id;
      assert(
        isUuid(templateId),
        "Eval summary template create did not return id.",
      );
      cleanup.defer("delete API journey eval summary template", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/eval-summary-templates/{template_id}/", {
              template_id: templateId,
            }),
          ),
        ),
      );
      assertEvalSummaryTemplatePayload(created, {
        templateId,
        name,
        description,
        criteria,
      });

      const afterCreateList = await client.get(
        apiPath("/model-hub/eval-summary-templates/"),
      );
      assertEvalSummaryTemplateList(afterCreateList, templateId);
      const createdAudit = await loadEvalSummaryTemplateDbAudit(
        templateId,
        organizationId,
      );
      assertEvalSummaryTemplateDbAudit(createdAudit, {
        templateId,
        organizationId,
        name,
        description,
        criteria,
        expectedExists: true,
      });

      const updated = await client.put(
        apiPath("/model-hub/eval-summary-templates/{template_id}/", {
          template_id: templateId,
        }),
        {
          name: updatedName,
          description: updatedDescription,
          criteria: updatedCriteria,
        },
      );
      assertEvalSummaryTemplatePayload(updated, {
        templateId,
        name: updatedName,
        description: updatedDescription,
        criteria: updatedCriteria,
      });
      const updatedAudit = await loadEvalSummaryTemplateDbAudit(
        templateId,
        organizationId,
      );
      assertEvalSummaryTemplateDbAudit(updatedAudit, {
        templateId,
        organizationId,
        name: updatedName,
        description: updatedDescription,
        criteria: updatedCriteria,
        expectedExists: true,
      });

      const deleted = await client.delete(
        apiPath("/model-hub/eval-summary-templates/{template_id}/", {
          template_id: templateId,
        }),
      );
      assert(
        deleted?.deleted === true,
        "Eval summary template delete did not return deleted=true.",
      );
      const postDeleteAudit = await loadEvalSummaryTemplateDbAudit(
        templateId,
        organizationId,
      );
      assertEvalSummaryTemplateDbAudit(postDeleteAudit, {
        templateId,
        organizationId,
        expectedExists: false,
      });

      evidence.push({
        eval_summary_template_id: templateId,
        initial_template_count: payloadArray(beforeList?.templates, "templates")
          .length,
        created_list_contains_template: true,
        db_row_removed_after_delete: postDeleteAudit.row_exists === false,
      });
    },
  },
  {
    id: "EVAL-API-013",
    title: "Eval playground execute, feedback, and log round trip",
    tags: [
      "evals",
      "eval-playground",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
      user,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_playground_${suffix}`;
      const firstFeedback = `api journey playground feedback ${suffix}`;
      const updatedFeedback = `${firstFeedback} updated`;
      const userId = currentUserId(user);

      const template = await createEvalPlaygroundCodeEval(client, templateName);
      const templateId = template.id;
      let templateDeleted = false;
      let logDeleted = false;
      let feedbackDeleted = false;
      let logId = null;
      let feedbackId = null;

      cleanup.defer("delete API journey eval playground feedback", async () => {
        if (!feedbackId || feedbackDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
          ),
        );
      });
      cleanup.defer("delete API journey eval playground log", async () => {
        if (!logId || logDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/get-eval-logs"), {
            body: { log_ids: [logId] },
          }),
        );
      });
      cleanup.defer("delete API journey eval playground template", async () => {
        if (templateDeleted) return null;
        return client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        });
      });

      const execution = await client.post(
        apiPath("/model-hub/eval-playground/"),
        {
          template_id: templateId,
          model: "",
          mapping: {
            output: "grounded answer",
            expected: "grounded answer",
          },
          config: { params: {} },
          input_data_types: {
            output: "text",
            expected: "text",
          },
        },
      );
      assertEvalPlaygroundExecutionPayload(execution);
      logId = execution.log_id;

      const logDetail = await client.get(apiPath("/model-hub/get-eval-logs"), {
        query: { log_id: logId },
      });
      assertEvalPlaygroundLogDetail(logDetail, logId);

      const evalConfig = await client.get(
        apiPath("/model-hub/get-eval-config"),
        {
          query: { eval_id: templateId },
        },
      );
      assertEvalPlaygroundConfigPayload(evalConfig, {
        templateId,
        templateName,
      });

      const evalNames = await client.post(
        apiPath("/model-hub/get-eval-template-names"),
        { search_text: templateName },
      );
      assertEvalPlaygroundTemplateNamePayload(evalNames, {
        templateId,
        templateName,
        expectedPresent: true,
      });

      const evalUsageTemplates = await client.post(
        apiPath("/model-hub/get-eval-templates"),
        {
          search_text: templateName,
          current_page_index: 0,
          page_size: 10,
          sort: [{ column_id: "updated_at", type: "descending" }],
        },
      );
      assertEvalPlaygroundUsageTemplateList(evalUsageTemplates, {
        templateId,
        templateName,
        expectedPresent: true,
      });

      const logTable = await client.get(
        apiPath("/model-hub/get-eval-logs-details"),
        {
          query: {
            eval_template_id: templateId,
            source: "eval_playground",
            current_page_index: 0,
            page_size: 10,
          },
        },
      );
      const outputColumn = assertEvalPlaygroundLogTable(logTable, {
        logId,
        expectedCellText: "grounded answer",
      });
      const filteredLogTable = await client.get(
        apiPath("/model-hub/get-eval-logs-details"),
        {
          query: {
            eval_template_id: templateId,
            source: "eval_playground",
            current_page_index: 0,
            page_size: 10,
            filters: JSON.stringify([
              {
                column_id: outputColumn.id,
                filter_config: {
                  filter_type: "text",
                  filter_op: "contains",
                  filter_value: "grounded",
                },
              },
            ]),
            sort: JSON.stringify([
              { column_id: "column1", type: "descending" },
            ]),
            search: { key: "grounded", type: ["text"] },
          },
        },
      );
      assertEvalPlaygroundLogTable(filteredLogTable, {
        logId,
        expectedCellText: "grounded answer",
        expectedSearchHighlight: true,
      });

      const updatedColumnConfig = logTable.column_config.map((column, index) =>
        index === 0 ? { ...column, is_visible: false } : column,
      );
      await client.patch(apiPath("/model-hub/get-eval-logs"), {
        eval_id: templateId,
        source: "eval_playground",
        column_config: updatedColumnConfig,
      });
      const patchedLogTable = await client.get(
        apiPath("/model-hub/get-eval-logs-details"),
        {
          query: {
            eval_template_id: templateId,
            source: "eval_playground",
            current_page_index: 0,
            page_size: 10,
          },
        },
      );
      assert(
        patchedLogTable?.column_config?.[0]?.is_visible === false,
        "Eval log column-config PATCH did not persist the hidden first column.",
      );
      if (isUuid(userId)) {
        const settingsAudit = await loadEvalPlaygroundSettingsAudit({
          templateId,
          userId,
          source: "eval_playground",
        });
        assertEvalPlaygroundSettingsAudit(settingsAudit, {
          expectedDeleted: false,
          expectedFirstColumnVisible: false,
        });
      }

      const feedback = await client.post(
        apiPath("/model-hub/eval-playground/feedback/"),
        {
          log_id: logId,
          action_type: "retune",
          value: "passed",
          explanation: firstFeedback,
        },
      );
      assert(
        isUuid(feedback?.feedback_id),
        "Feedback create did not return id.",
      );
      assert(
        String(feedback?.message || "").includes("retuning"),
        "Feedback create did not return the retuning message.",
      );
      feedbackId = feedback.feedback_id;

      const feedbackUpdate = await client.post(
        apiPath("/model-hub/eval-playground/feedback/"),
        {
          log_id: logId,
          action_type: "retune",
          value: "failed",
          explanation: updatedFeedback,
        },
      );
      assert(
        feedbackUpdate?.feedback_id === feedbackId,
        "Feedback update created a duplicate feedback row for the same log.",
      );

      const feedbackList = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/feedback-list/", {
          template_id: templateId,
        }),
        { query: { page: 0, page_size: 5 } },
      );
      assertEvalPlaygroundFeedbackList(
        feedbackList,
        feedbackId,
        updatedFeedback,
      );

      const activeAudit = await loadEvalPlaygroundDbAudit({
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
      });
      assertEvalPlaygroundDbAudit(activeAudit, {
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedFeedbackExplanation: updatedFeedback,
      });

      await client.delete(
        apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
      );
      feedbackDeleted = true;
      await client.delete(apiPath("/model-hub/get-eval-logs"), {
        body: { log_ids: [logId] },
      });
      logDeleted = true;
      const deletedTemplate = await client.post(
        apiPath("/model-hub/eval-templates/bulk-delete/"),
        { template_ids: [templateId] },
      );
      templateDeleted = true;
      assert(
        Number(deletedTemplate?.deleted_count) === 1,
        "Eval playground template bulk delete did not delete the template.",
      );

      const postDeleteNames = await client.post(
        apiPath("/model-hub/get-eval-template-names"),
        { search_text: templateName },
      );
      assertEvalPlaygroundTemplateNamePayload(postDeleteNames, {
        templateId,
        templateName,
        expectedPresent: false,
      });
      const postDeleteUsageTemplates = await client.post(
        apiPath("/model-hub/get-eval-templates"),
        {
          search_text: templateName,
          current_page_index: 0,
          page_size: 10,
        },
      );
      assertEvalPlaygroundUsageTemplateList(postDeleteUsageTemplates, {
        templateId,
        templateName,
        expectedPresent: false,
      });

      const deletedAudit = await loadEvalPlaygroundDbAudit({
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
      });
      assertEvalPlaygroundDbAudit(deletedAudit, {
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedFeedbackExplanation: updatedFeedback,
      });
      if (isUuid(userId)) {
        const deletedSettingsAudit = await loadEvalPlaygroundSettingsAudit({
          templateId,
          userId,
          source: "eval_playground",
        });
        assertEvalPlaygroundSettingsAudit(deletedSettingsAudit, {
          expectedDeleted: true,
          expectedFirstColumnVisible: false,
        });
      }

      evidence.push({
        eval_template_id: templateId,
        eval_playground_log_id: logId,
        feedback_id: feedbackId,
        output: execution.output,
        eval_config_loaded: evalConfig?.eval?.id === templateId,
        eval_picker_loaded_template: true,
        eval_usage_list_loaded_template: true,
        log_table_rows: filteredLogTable?.metadata?.total_rows ?? 0,
        log_deleted_at_set: deletedAudit.log_deleted_at_set,
        template_deleted_at_set: deletedAudit.template_deleted_at_set,
        feedback_absent_or_deleted: deletedAudit.feedback_absent_or_deleted,
      });
    },
  },
  {
    id: "EVAL-API-014",
    title: "Eval playground recalculate feedback and error-localizer polling",
    tags: [
      "evals",
      "eval-playground",
      "error-localizer",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_playground_localizer_${suffix}`;
      const feedbackText = `api journey recalculate feedback ${suffix}`;

      const template = await createEvalPlaygroundCodeEval(client, templateName);
      const templateId = template.id;
      let templateDeleted = false;
      let logDeleted = false;
      let feedbackDeleted = false;
      let logId = null;
      let feedbackId = null;

      cleanup.defer("delete API journey eval localizer feedback", async () => {
        if (!feedbackId || feedbackDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
          ),
        );
      });
      cleanup.defer("delete API journey eval localizer log", async () => {
        if (!logId || logDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/get-eval-logs"), {
            body: { log_ids: [logId] },
          }),
        );
      });
      cleanup.defer("delete API journey eval localizer template", async () => {
        if (templateDeleted) return null;
        return client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [templateId],
        });
      });

      const execution = await client.post(
        apiPath("/model-hub/eval-playground/"),
        {
          template_id: templateId,
          model: "",
          error_localizer: true,
          mapping: {
            output: "ungrounded answer",
            expected: "grounded answer",
          },
          config: { params: {} },
          input_data_types: {
            output: "text",
            expected: "text",
          },
        },
      );
      assertEvalPlaygroundExecutionPayload(execution);
      assert(
        String(execution.output || "").toLowerCase() === "failed",
        "Error-localizer playground run did not produce a failing eval result.",
      );
      logId = execution.log_id;

      const logDetail = await client.get(apiPath("/model-hub/get-eval-logs"), {
        query: { log_id: logId },
      });
      assertEvalPlaygroundLogDetail(logDetail, logId);
      assertEvalPlaygroundErrorLocalizerDetail(logDetail, logId);

      const localizerAudit = await loadEvalPlaygroundErrorLocalizerAudit({
        templateId,
        logId,
        organizationId,
        workspaceId,
      });
      assertEvalPlaygroundErrorLocalizerAudit(localizerAudit, {
        templateId,
        logId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
      });

      const feedback = await client.post(
        apiPath("/model-hub/eval-playground/feedback/"),
        {
          log_id: logId,
          action_type: "recalculate",
          value: "failed",
          explanation: feedbackText,
        },
      );
      assert(
        isUuid(feedback?.feedback_id),
        "Recalculate feedback did not return id.",
      );
      assert(
        String(feedback?.message || "").includes("recalculation"),
        "Recalculate feedback did not return the recalculation message.",
      );
      feedbackId = feedback.feedback_id;

      const feedbackList = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/feedback-list/", {
          template_id: templateId,
        }),
        { query: { page: 0, page_size: 5 } },
      );
      assertEvalPlaygroundFeedbackList(feedbackList, feedbackId, feedbackText, {
        expectedActionType: "recalculate",
      });

      const activeAudit = await loadEvalPlaygroundDbAudit({
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
      });
      assertEvalPlaygroundDbAudit(activeAudit, {
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedFeedbackExplanation: feedbackText,
        expectedFeedbackActionType: "recalculate",
      });

      await client.delete(
        apiPath("/model-hub/feedback/{id}/", { id: feedbackId }),
      );
      feedbackDeleted = true;
      await client.delete(apiPath("/model-hub/get-eval-logs"), {
        body: { log_ids: [logId] },
      });
      logDeleted = true;
      const deletedLocalizerAudit = await loadEvalPlaygroundErrorLocalizerAudit(
        {
          templateId,
          logId,
          organizationId,
          workspaceId,
        },
      );
      assertEvalPlaygroundErrorLocalizerAudit(deletedLocalizerAudit, {
        templateId,
        logId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
      });

      const deletedTemplate = await client.post(
        apiPath("/model-hub/eval-templates/bulk-delete/"),
        { template_ids: [templateId] },
      );
      templateDeleted = true;
      assert(
        Number(deletedTemplate?.deleted_count) === 1,
        "Eval localizer template bulk delete did not delete the template.",
      );

      const deletedAudit = await loadEvalPlaygroundDbAudit({
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
      });
      assertEvalPlaygroundDbAudit(deletedAudit, {
        templateId,
        logId,
        feedbackId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedFeedbackExplanation: feedbackText,
        expectedFeedbackActionType: "recalculate",
      });

      evidence.push({
        eval_template_id: templateId,
        eval_playground_log_id: logId,
        feedback_id: feedbackId,
        output: execution.output,
        error_localizer_status: logDetail.error_localizer_status,
        error_localizer_task_deleted_at_set:
          deletedLocalizerAudit.task_deleted_at_set,
        feedback_action_type: activeAudit.feedback_action_type,
        log_deleted_at_set: deletedAudit.log_deleted_at_set,
        template_deleted_at_set: deletedAudit.template_deleted_at_set,
      });
    },
  },
  {
    id: "EVAL-API-017",
    title: "Test evaluation template pass/fail and cleanup contract",
    tags: [
      "evals",
      "test-evaluation",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_test_eval_${suffix}`;

      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip(
          "System eval word_count_in_range was not available for test-evaluation.",
        );
      }
      const templateId = template.id;
      const evalTypeId =
        template?.config?.eval_type_id ||
        template?.eval_type_id ||
        template.name;
      let logsDeleted = false;
      let passLogId = null;
      let failLogId = null;

      cleanup.defer("delete API journey test-evaluation logs", async () => {
        const logIds = [passLogId, failLogId].filter(isUuid);
        if (!logIds.length || logsDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/get-eval-logs"), {
            body: { log_ids: logIds },
          }),
        );
      });

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/test-evaluation/"), {
            name: `${templateName}_missing_type`,
            template_type: "Function",
            template_id: templateId,
            output_type: "Pass/Fail",
            config: {
              mapping: { output: "same", expected: "same" },
              config: {},
            },
          }),
        400,
        "test-evaluation should reject Function payloads without eval_type_id.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/test-evaluation/"), {
            name: `${templateName}_unsupported`,
            template_type: "Unsupported",
            output_type: "Pass/Fail",
            config: {},
          }),
        400,
        "test-evaluation should reject unsupported template_type values.",
      );

      const passRun = await client.post(
        apiPath("/model-hub/test-evaluation/"),
        testEvaluationPayload({
          name: `${templateName}_pass`,
          templateId,
          evalTypeId,
          mapping: { text: "one two three four" },
          params: { min_words: 1, max_words: 8 },
        }),
      );
      passLogId = passRun.log_id;
      assertTestEvaluationPayload(passRun, "Passed");

      const failRun = await client.post(
        apiPath("/model-hub/test-evaluation/"),
        testEvaluationPayload({
          name: `${templateName}_fail`,
          templateId,
          evalTypeId,
          mapping: { text: "one two three four" },
          params: { min_words: 10, max_words: 20 },
        }),
      );
      failLogId = failRun.log_id;
      assertTestEvaluationPayload(failRun, "Failed");

      const activeAudit = await loadTestEvaluationDbAudit({
        templateId,
        logIds: [passLogId, failLogId],
        organizationId,
        workspaceId,
      });
      assertTestEvaluationDbAudit(activeAudit, {
        templateId,
        evalTypeId,
        logIds: [passLogId, failLogId],
        organizationId,
        workspaceId,
        expectedLogsDeleted: false,
      });

      await client.delete(apiPath("/model-hub/get-eval-logs"), {
        body: { log_ids: [passLogId, failLogId] },
      });
      logsDeleted = true;

      const deletedAudit = await loadTestEvaluationDbAudit({
        templateId,
        logIds: [passLogId, failLogId],
        organizationId,
        workspaceId,
      });
      assertTestEvaluationDbAudit(deletedAudit, {
        templateId,
        evalTypeId,
        logIds: [passLogId, failLogId],
        organizationId,
        workspaceId,
        expectedLogsDeleted: true,
      });

      evidence.push({
        eval_template_id: templateId,
        eval_template_name: template.name,
        eval_type_id: evalTypeId,
        pass_log_id: passLogId,
        fail_log_id: failLogId,
        pass_output: passRun.output,
        fail_output: failRun.output,
        log_sources: activeAudit.logs.map((row) => row.source),
        logs_deleted_at_set: deletedAudit.logs.every(
          (row) => row.deleted_at_set === true,
        ),
      });
    },
  },
  {
    id: "EVAL-API-012",
    title: "Composite eval create, update, execute, and delete lifecycle",
    tags: ["evals", "composite", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const childOneName = `api_journey_composite_child_a_${suffix}`;
      const childTwoName = `api_journey_composite_child_b_${suffix}`;
      const compositeName = `api_journey_composite_${suffix}`;
      const updatedCompositeName = `${compositeName}_updated`;

      const childOne = await createCompositeChildEval(
        client,
        cleanup,
        childOneName,
        "return True",
      );
      const childTwo = await createCompositeChildEval(
        client,
        cleanup,
        childTwoName,
        "return output == expected",
      );
      const childOneVersion = await firstEvalTemplateVersion(
        client,
        childOne.id,
      );
      const childIds = [childOne.id, childTwo.id];
      const pinnedVersionIds = { [childOne.id]: childOneVersion.id };

      const created = await client.post(
        apiPath("/model-hub/eval-templates/create-composite/"),
        {
          name: compositeName,
          description: "Created by API journey composite regression.",
          tags: ["api-journey", "composite"],
          child_template_ids: childIds,
          aggregation_enabled: true,
          aggregation_function: "weighted_avg",
          composite_child_axis: "pass_fail",
          child_weights: {
            [childOne.id]: 1.0,
            [childTwo.id]: 2.0,
          },
          child_pinned_versions: pinnedVersionIds,
        },
      );
      const compositeId = created?.id;
      assert(
        isUuid(compositeId),
        "Composite eval create did not return a UUID id.",
      );
      cleanup.defer("delete API journey composite eval", () =>
        client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [compositeId],
        }),
      );
      assertCompositeDetail(created, {
        compositeId,
        name: compositeName,
        childIds,
        aggregationFunction: "weighted_avg",
        weights: { [childOne.id]: 1.0, [childTwo.id]: 2.0 },
        pinnedVersionIds,
      });

      const detail = await client.get(
        apiPath("/model-hub/eval-templates/{template_id}/composite/", {
          template_id: compositeId,
        }),
      );
      assertCompositeDetail(detail, {
        compositeId,
        name: compositeName,
        childIds,
        aggregationFunction: "weighted_avg",
        weights: { [childOne.id]: 1.0, [childTwo.id]: 2.0 },
        pinnedVersionIds,
      });

      const updated = await client.patch(
        apiPath("/model-hub/eval-templates/{template_id}/composite/", {
          template_id: compositeId,
        }),
        {
          name: updatedCompositeName,
          description: "Updated by API journey composite regression.",
          aggregation_enabled: true,
          aggregation_function: "pass_rate",
          child_weights: {
            [childOne.id]: 3.0,
            [childTwo.id]: 1.0,
          },
        },
      );
      assertCompositeDetail(updated, {
        compositeId,
        name: updatedCompositeName,
        childIds,
        aggregationFunction: "pass_rate",
        weights: { [childOne.id]: 3.0, [childTwo.id]: 1.0 },
        pinnedVersionIds,
      });
      assert(
        Number(updated.version_number) >= 2,
        "Composite update did not create a new version.",
      );

      const listPayload = await client.post(
        apiPath("/model-hub/eval-templates/list/"),
        {
          page: 0,
          page_size: 10,
          owner_filter: "user",
          search: updatedCompositeName,
          filters: { template_type: ["composite"] },
          sort_by: "updated_at",
          sort_order: "desc",
        },
      );
      const listItems = payloadArray(listPayload?.items, "items");
      assert(
        listItems.some(
          (item) =>
            item.id === compositeId && item.template_type === "composite",
        ),
        "Eval template list did not include the updated composite eval.",
      );

      const executePayload = {
        mapping: {
          output: "approved",
          expected: "approved",
        },
        config: {},
        input_data_types: { output: "text", expected: "text" },
      };
      const executed = await client.post(
        apiPath("/model-hub/eval-templates/{template_id}/composite/execute/", {
          template_id: compositeId,
        }),
        executePayload,
      );
      assertCompositeExecutePayload(executed, {
        compositeId,
        name: updatedCompositeName,
        childIds,
      });

      const adhocExecuted = await client.post(
        apiPath("/model-hub/eval-templates/composite/execute-adhoc/"),
        {
          ...executePayload,
          child_template_ids: childIds,
          aggregation_enabled: true,
          aggregation_function: "pass_rate",
          composite_child_axis: "pass_fail",
          child_weights: {
            [childOne.id]: 3.0,
            [childTwo.id]: 1.0,
          },
          pass_threshold: 0.5,
        },
      );
      assertCompositeExecutePayload(adhocExecuted, {
        compositeId: "",
        name: "(adhoc-composite)",
        childIds,
      });

      const activeAudit = await loadCompositeEvalDbAudit(
        compositeId,
        organizationId,
        workspaceId,
      );
      assertCompositeEvalDbAudit(activeAudit, {
        compositeId,
        childIds,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedActiveChildren: 2,
        expectedVersions: 2,
      });

      const deleted = await client.post(
        apiPath("/model-hub/eval-templates/bulk-delete/"),
        { template_ids: [compositeId] },
      );
      assert(
        Number(deleted?.deleted_count) === 1,
        "Composite eval bulk delete did not delete the composite.",
      );

      const deletedAudit = await loadCompositeEvalDbAudit(
        compositeId,
        organizationId,
        workspaceId,
      );
      assertCompositeEvalDbAudit(deletedAudit, {
        compositeId,
        childIds,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedActiveChildren: 2,
        expectedVersions: 2,
      });

      evidence.push({
        composite_eval_id: compositeId,
        composite_name: updatedCompositeName,
        child_eval_ids: childIds,
        pinned_child_version_id: childOneVersion.id,
        execute_completed_children: executed.completed_children,
        execute_failed_children: executed.failed_children,
        adhoc_completed_children: adhocExecuted.completed_children,
        deleted_at_set_after_cleanup: deletedAudit.deleted_at_set === true,
      });
    },
  },
  {
    id: "EVAL-API-015",
    title:
      "Composite eval dataset binding validates child mappings and cleans up metric",
    tags: ["evals", "composite", "dataset", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const datasetName = `api journey composite binding ${runId}`;
      const outputName = `api_journey_output_${suffix}`;
      const expectedName = `api_journey_expected_${suffix}`;
      const metricName = `api_journey_cmp_${suffix}`;
      const childOneName = `api_journey_binding_child_a_${suffix}`;
      const childTwoName = `api_journey_binding_child_b_${suffix}`;
      const compositeName = `api_journey_binding_composite_${suffix}`;

      const dataset = await createOrResolveWritableDataset(
        client,
        cleanup,
        datasetName,
        evidence,
      );
      const datasetId = dataset.id;

      let metricDeleted = false;
      const outputColumnDeleted = false;
      const expectedColumnDeleted = false;

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
          dataset_id: datasetId,
        }),
        {
          new_column_name: outputName,
          column_type: "text",
          source: "OTHERS",
        },
      );
      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
          dataset_id: datasetId,
        }),
        {
          new_column_name: expectedName,
          column_type: "text",
          source: "OTHERS",
        },
      );

      const table = await getDatasetTable(client, datasetId, {
        column_config_only: true,
      });
      const outputColumn = findColumn(table, outputName);
      const expectedColumn = findColumn(table, expectedName);
      assert(
        outputColumn?.id,
        "Composite binding output column was not visible.",
      );
      assert(
        expectedColumn?.id,
        "Composite binding expected column was not visible.",
      );
      cleanup.defer("delete composite binding expected column", async () => {
        if (expectedColumnDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
              {
                dataset_id: datasetId,
                column_id: expectedColumn.id,
              },
            ),
          ),
        );
      });
      cleanup.defer("delete composite binding output column", async () => {
        if (outputColumnDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_column/{column_id}/",
              {
                dataset_id: datasetId,
                column_id: outputColumn.id,
              },
            ),
          ),
        );
      });

      const childOne = await createCompositeRequiredKeyChildEval(
        client,
        cleanup,
        childOneName,
        "Check that {{output}} is present.",
      );
      const childTwo = await createCompositeRequiredKeyChildEval(
        client,
        cleanup,
        childTwoName,
        "Check that {{expected}} is present.",
      );
      const childIds = [childOne.id, childTwo.id];

      const createdComposite = await client.post(
        apiPath("/model-hub/eval-templates/create-composite/"),
        {
          name: compositeName,
          description: "Composite dataset binding regression.",
          tags: ["api-journey", "composite-binding"],
          child_template_ids: childIds,
          aggregation_enabled: true,
          aggregation_function: "pass_rate",
          composite_child_axis: "pass_fail",
        },
      );
      const compositeId = createdComposite?.id;
      assert(
        isUuid(compositeId),
        "Composite binding eval create did not return a UUID id.",
      );
      cleanup.defer("delete API journey binding composite eval", () =>
        client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
          template_ids: [compositeId],
        }),
      );

      let missingMappingError = null;
      try {
        await client.post(
          apiPath("/model-hub/develops/{dataset_id}/add_user_eval/", {
            dataset_id: datasetId,
          }),
          {
            name: `${metricName}_missing`,
            template_id: compositeId,
            config: {
              mapping: {
                output: outputColumn.id,
              },
            },
            run: false,
            model: "turing_small",
          },
        );
      } catch (error) {
        missingMappingError = error;
      }
      assert(
        missingMappingError?.status === 400,
        "Composite dataset binding did not reject a missing child required key.",
      );
      const missingMappingText = [
        missingMappingError.message,
        JSON.stringify(missingMappingError.body || {}),
      ].join(" ");
      assert(
        missingMappingText.includes("expected"),
        "Composite dataset binding missing-key error did not name the missing key.",
      );

      await client.post(
        apiPath("/model-hub/develops/{dataset_id}/add_user_eval/", {
          dataset_id: datasetId,
        }),
        {
          name: metricName,
          template_id: compositeId,
          config: {
            mapping: {
              output: outputColumn.id,
              expected: expectedColumn.id,
            },
          },
          run: false,
          model: "turing_small",
          error_localizer: false,
          composite_weight_overrides: {
            [childOne.id]: 0.25,
            [childTwo.id]: 0.75,
          },
        },
      );

      const activeAudit = await loadCompositeDatasetBindingDbAudit({
        datasetId,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
      });
      assertCompositeDatasetBindingDbAudit(activeAudit, {
        datasetId,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
        mapping: {
          output: outputColumn.id,
          expected: expectedColumn.id,
        },
        weightOverrides: {
          [childOne.id]: 0.25,
          [childTwo.id]: 0.75,
        },
        expectedDeleted: false,
      });

      const metric = payloadArray(activeAudit.metrics, "metrics")[0];
      const metricId = metric?.id;
      assert(
        isUuid(metricId),
        "Composite dataset binding DB audit found no metric.",
      );
      cleanup.defer("delete API journey composite dataset metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
              {
                dataset_id: datasetId,
                eval_id: metricId,
              },
            ),
            { body: { delete_column: true }, okStatuses: [200, 404] },
          ),
        );
      });

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          {
            dataset_id: datasetId,
            eval_id: metricId,
          },
        ),
        { body: { delete_column: true } },
      );
      metricDeleted = true;

      const deletedAudit = await loadCompositeDatasetBindingDbAudit({
        datasetId,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
      });
      assertCompositeDatasetBindingDbAudit(deletedAudit, {
        datasetId,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
        mapping: {
          output: outputColumn.id,
          expected: expectedColumn.id,
        },
        weightOverrides: {
          [childOne.id]: 0.25,
          [childTwo.id]: 0.75,
        },
        expectedDeleted: true,
      });

      evidence.push({
        dataset_id: datasetId,
        composite_eval_id: compositeId,
        composite_metric_id: metricId,
        child_eval_ids: childIds,
        rejected_missing_key: "expected",
        active_metric_status: metric.status,
        deleted_at_set_after_cleanup: deletedAudit.metrics[0]?.deleted_at_set,
      });
    },
  },
  {
    id: "EVAL-API-016",
    title:
      "Composite eval experiment binding validates child mappings and cleans up metric",
    tags: ["evals", "composite", "experiments", "mutating", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const metricName = `api_journey_exp_cmp_${suffix}`;
      const childOneName = `api_journey_exp_binding_child_a_${suffix}`;
      const childTwoName = `api_journey_exp_binding_child_b_${suffix}`;
      const compositeName = `api_journey_exp_binding_composite_${suffix}`;

      const { experiment, mapping, mappedColumns } =
        await resolveCompositeExperimentBindingFixtures(
          client,
          organizationId,
          workspaceId,
        );

      let metricDeleted = false;
      const childOne = await createCompositeRequiredKeyChildEval(
        client,
        cleanup,
        childOneName,
        "Check experiment {{output}}.",
      );
      const childTwo = await createCompositeRequiredKeyChildEval(
        client,
        cleanup,
        childTwoName,
        "Check experiment {{expected}}.",
      );
      const childIds = [childOne.id, childTwo.id];

      const createdComposite = await client.post(
        apiPath("/model-hub/eval-templates/create-composite/"),
        {
          name: compositeName,
          description: "Composite experiment binding regression.",
          tags: ["api-journey", "composite-binding", "experiment"],
          child_template_ids: childIds,
          aggregation_enabled: true,
          aggregation_function: "pass_rate",
          composite_child_axis: "pass_fail",
        },
      );
      const compositeId = createdComposite?.id;
      assert(
        isUuid(compositeId),
        "Composite experiment binding eval create did not return a UUID id.",
      );
      cleanup.defer(
        "delete API journey experiment binding composite eval",
        () =>
          client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
            template_ids: [compositeId],
          }),
      );

      let missingMappingError = null;
      try {
        await client.post(
          apiPath("/model-hub/experiments/{experiment_id}/add-eval/", {
            experiment_id: experiment.id,
          }),
          {
            name: `${metricName}_missing`,
            template_id: compositeId,
            config: {
              mapping: {
                output: mapping.output,
              },
            },
            run: false,
            model: "turing_small",
          },
        );
      } catch (error) {
        missingMappingError = error;
      }
      assert(
        missingMappingError?.status === 400,
        "Composite experiment binding did not reject a missing child required key.",
      );
      const missingMappingText = [
        missingMappingError.message,
        JSON.stringify(missingMappingError.body || {}),
      ].join(" ");
      assert(
        missingMappingText.includes("expected"),
        "Composite experiment binding missing-key error did not name the missing key.",
      );

      const createdMetric = await client.post(
        apiPath("/model-hub/experiments/{experiment_id}/add-eval/", {
          experiment_id: experiment.id,
        }),
        {
          name: metricName,
          template_id: compositeId,
          config: { mapping },
          run: false,
          model: "turing_small",
          error_localizer: false,
          composite_weight_overrides: {
            [childOne.id]: 0.35,
            [childTwo.id]: 0.65,
          },
        },
      );
      const metricId = createdMetric?.eval_id;
      assert(
        isUuid(metricId),
        "Composite experiment binding add-eval did not return a metric UUID.",
      );
      cleanup.defer(
        "delete API journey composite experiment metric",
        async () => {
          if (metricDeleted) return null;
          return ignoreNotFound(() =>
            client.delete(
              apiPath(
                "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
                {
                  dataset_id: experiment.dataset_id,
                  eval_id: metricId,
                },
              ),
              {
                body: { delete_column: true, experiment_id: experiment.id },
                okStatuses: [200, 400, 404],
              },
            ),
          );
        },
      );

      const activeAudit = await loadCompositeExperimentBindingDbAudit({
        experimentId: experiment.id,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
      });
      assertCompositeExperimentBindingDbAudit(activeAudit, {
        experimentId: experiment.id,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
        mapping,
        weightOverrides: {
          [childOne.id]: 0.35,
          [childTwo.id]: 0.65,
        },
        expectedDeleted: false,
      });

      const metric = payloadArray(activeAudit.metrics, "metrics")[0];
      assert(
        metric?.id === metricId,
        "Composite experiment binding DB audit returned a different metric id.",
      );
      const deleteDatasetId = metric.dataset_id || activeAudit.eval_dataset_id;

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          {
            dataset_id: deleteDatasetId,
            eval_id: metricId,
          },
        ),
        { body: { delete_column: true, experiment_id: experiment.id } },
      );
      metricDeleted = true;

      const deletedAudit = await loadCompositeExperimentBindingDbAudit({
        experimentId: experiment.id,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
      });
      assertCompositeExperimentBindingDbAudit(deletedAudit, {
        experimentId: experiment.id,
        templateId: compositeId,
        metricName,
        organizationId,
        workspaceId,
        mapping,
        weightOverrides: {
          [childOne.id]: 0.35,
          [childTwo.id]: 0.65,
        },
        expectedDeleted: true,
      });

      evidence.push({
        experiment_id: experiment.id,
        experiment_name: experiment.name,
        dataset_id: experiment.dataset_id,
        eval_dataset_id: activeAudit.eval_dataset_id,
        composite_eval_id: compositeId,
        composite_metric_id: metricId,
        child_eval_ids: childIds,
        rejected_missing_key: "expected",
        mapped_columns: mappedColumns,
        active_metric_status: metric.status,
        deleted_at_set_after_cleanup: deletedAudit.metrics[0]?.deleted_at_set,
      });
    },
  },
  {
    id: "EVAL-API-006",
    title: "Legacy eval group CRUD and member lifecycle",
    tags: [
      "evals",
      "legacy-eval-groups",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const name = `api_journey_eval_group_${suffix}`;
      const updatedName = `${name}_updated`;
      const description = "Eval group created by API journey.";
      const patchedDescription = "Eval group patched by API journey.";
      const updatedDescription = "Eval group updated by API journey.";

      const templatesPayload = await client.post(
        apiPath("/model-hub/eval-templates/list/"),
        {
          page: 0,
          page_size: 20,
          owner_filter: "all",
          sort_by: "updated_at",
          sort_order: "desc",
        },
      );
      const templates = payloadArray(templatesPayload?.items, "items").filter(
        (template) => isUuid(template?.id),
      );
      if (templates.length < 2)
        skip(
          "At least two eval templates are required for eval group coverage.",
        );
      const [firstTemplate, secondTemplate] = templates;

      let groupDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name,
        description,
        eval_template_ids: [firstTemplate.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer("delete API journey eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
          ),
        );
      });
      assertEvalGroupCreatePayload(created, {
        groupId,
        name,
        description,
      });

      const list = await client.get(apiPath("/model-hub/eval-groups/"), {
        query: { name, page_number: 0, page_size: 12 },
      });
      assertEvalGroupListPayload(list, groupId, 1);

      const detail = await client.get(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      assertEvalGroupDetailPayload(detail, {
        groupId,
        name,
        description,
        expectedTemplateIds: [firstTemplate.id],
      });
      const createdAudit = await loadEvalGroupDbAudit(
        groupId,
        organizationId,
        workspaceId,
      );
      assertEvalGroupDbAudit(createdAudit, {
        groupId,
        organizationId,
        workspaceId,
        name,
        description,
        expectedTemplateIds: [firstTemplate.id],
        expectedDeleted: false,
        expectedAddHistory: 1,
        expectedDeleteHistory: 0,
      });

      await client.post(apiPath("/model-hub/eval-groups/edit-eval-list/"), {
        eval_group_id: groupId,
        added_template_ids: [secondTemplate.id],
      });
      const afterAdd = await client.get(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      assertEvalGroupDetailPayload(afterAdd, {
        groupId,
        name,
        description,
        expectedTemplateIds: [firstTemplate.id, secondTemplate.id],
      });

      await client.post(apiPath("/model-hub/eval-groups/edit-eval-list/"), {
        eval_group_id: groupId,
        deleted_template_ids: [firstTemplate.id],
      });
      const afterRemove = await client.get(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      assertEvalGroupDetailPayload(afterRemove, {
        groupId,
        name,
        description,
        expectedTemplateIds: [secondTemplate.id],
      });

      const patched = await client.patch(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
        { description: patchedDescription },
      );
      assert(
        patched?.description === patchedDescription,
        "Eval group PATCH did not persist description.",
      );

      const updated = await client.put(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
        { name: updatedName, description: updatedDescription },
      );
      assert(
        updated?.name === updatedName &&
          updated?.description === updatedDescription,
        "Eval group PUT did not persist name and description.",
      );

      const updatedAudit = await loadEvalGroupDbAudit(
        groupId,
        organizationId,
        workspaceId,
      );
      assertEvalGroupDbAudit(updatedAudit, {
        groupId,
        organizationId,
        workspaceId,
        name: updatedName,
        description: updatedDescription,
        expectedTemplateIds: [secondTemplate.id],
        expectedDeleted: false,
        expectedAddHistory: 2,
        expectedDeleteHistory: 1,
      });

      const deleted = await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      assert(
        String(deleted).includes("deleted successfully"),
        "Eval group delete did not return the expected success message.",
      );
      groupDeleted = true;
      const deletedAudit = await loadEvalGroupDbAudit(
        groupId,
        organizationId,
        workspaceId,
      );
      assertEvalGroupDbAudit(deletedAudit, {
        groupId,
        organizationId,
        workspaceId,
        name: updatedName,
        description: updatedDescription,
        expectedTemplateIds: [],
        expectedDeleted: true,
        expectedAddHistory: 2,
        expectedDeleteHistory: 1,
      });

      evidence.push({
        eval_group_id: groupId,
        initial_template_id: firstTemplate.id,
        remaining_template_id: secondTemplate.id,
        add_history_count: Number(deletedAudit.add_history_count),
        delete_history_count: Number(deletedAudit.delete_history_count),
        deleted_at_set: deletedAudit.deleted_at_set,
        relationship_count_after_delete: Number(
          deletedAudit.relationship_count,
        ),
      });
    },
  },
  {
    id: "EVAL-API-007",
    title: "Apply eval group to dataset and delete generated metric",
    tags: ["evals", "dataset", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_apply_group_${suffix}`;
      const description = "Eval group dataset fanout created by API journey.";

      const { dataset, columnsByName, template, requiredKeys, mapping } =
        await resolveEvalGroupDatasetApplyFixtures(client);

      let groupDeleted = false;
      let metricDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name: groupName,
        description,
        eval_template_ids: [template.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer("delete API journey apply eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
          ),
        );
      });

      const applyResult = await client.post(
        apiPath("/model-hub/eval-groups/apply-eval-group/"),
        {
          eval_group_id: groupId,
          page_id: "DATASET",
          filters: {
            dataset_id: dataset.id,
            model: "turing_small",
            error_localizer: false,
          },
          mapping,
          params: {},
        },
      );
      assert(
        applyResult === null || applyResult === undefined,
        "Dataset apply unexpectedly returned a non-empty payload.",
      );

      const appliedAudit = await loadEvalGroupDatasetApplyDbAudit(
        groupId,
        dataset.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupDatasetApplyDbAudit(appliedAudit, {
        groupId,
        datasetId: dataset.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        mapping,
        expectedDeleted: false,
      });

      const metric = payloadArray(appliedAudit.metrics, "metrics")[0];
      const metricId = metric?.id;
      assert(
        isUuid(metricId),
        "Dataset apply DB audit did not return metric id.",
      );
      cleanup.defer("delete API journey dataset eval metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
              {
                dataset_id: dataset.id,
                eval_id: metricId,
              },
            ),
            { body: { delete_column: true }, okStatuses: [200, 404] },
          ),
        );
      });

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          {
            dataset_id: dataset.id,
            eval_id: metricId,
          },
        ),
        { body: { delete_column: true } },
      );
      metricDeleted = true;

      const deletedMetricAudit = await loadEvalGroupDatasetApplyDbAudit(
        groupId,
        dataset.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupDatasetApplyDbAudit(deletedMetricAudit, {
        groupId,
        datasetId: dataset.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        mapping,
        expectedDeleted: true,
      });

      await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      groupDeleted = true;

      evidence.push({
        dataset_id: dataset.id,
        dataset_name: dataset.name,
        eval_group_id: groupId,
        eval_template_id: template.id,
        required_keys: requiredKeys,
        mapped_columns: Object.fromEntries(
          Object.entries(mapping).map(([key, columnId]) => [
            key,
            columnsByName.get(key)?.name || columnId,
          ]),
        ),
        created_metric_id: metricId,
        active_metric_count_after_apply: Number(appliedAudit.active_count),
        deleted_metric_count_after_cleanup: Number(
          deletedMetricAudit.deleted_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedMetricAudit.metrics, "metrics")[0]
            ?.deleted_at_set === true,
      });
    },
  },
  {
    id: "EVAL-API-008",
    title: "Apply eval group to prompt template and delete generated config",
    tags: ["evals", "prompts", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_prompt_apply_group_${suffix}`;
      const description = "Eval group prompt fanout created by API journey.";

      const { prompt, template, requiredKeys, mapping } =
        await resolveEvalGroupPromptApplyFixtures(client);

      let groupDeleted = false;
      let configDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name: groupName,
        description,
        eval_template_ids: [template.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer("delete API journey prompt apply eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
          ),
        );
      });

      const appliedConfigs = payloadArray(
        await client.post(apiPath("/model-hub/eval-groups/apply-eval-group/"), {
          eval_group_id: groupId,
          page_id: "PROMPT",
          filters: {
            prompt_template_id: prompt.id,
            error_localizer: false,
          },
          mapping,
          params: {},
        }),
        "configs",
      );
      const appliedConfig = appliedConfigs.find(
        (config) => config?.eval_template_id === template.id,
      );
      assert(
        isUuid(appliedConfig?.id),
        "Prompt apply did not return a prompt eval config id.",
      );
      assertPromptApplyPayload(appliedConfig, {
        templateId: template.id,
        mapping,
      });

      const configId = appliedConfig.id;
      cleanup.defer("delete API journey prompt eval config", async () => {
        if (configDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            `${apiPath(
              "/model-hub/prompt-templates/{id}/delete-evaluation-config/",
              {
                id: prompt.id,
              },
            )}?id=${encodeURIComponent(configId)}`,
            { okStatuses: [200, 400, 404] },
          ),
        );
      });

      const appliedAudit = await loadEvalGroupPromptApplyDbAudit(
        groupId,
        prompt.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupPromptApplyDbAudit(appliedAudit, {
        groupId,
        promptId: prompt.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: false,
      });

      await client.delete(
        `${apiPath(
          "/model-hub/prompt-templates/{id}/delete-evaluation-config/",
          {
            id: prompt.id,
          },
        )}?id=${encodeURIComponent(configId)}`,
      );
      configDeleted = true;

      const deletedConfigAudit = await loadEvalGroupPromptApplyDbAudit(
        groupId,
        prompt.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupPromptApplyDbAudit(deletedConfigAudit, {
        groupId,
        promptId: prompt.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: true,
      });

      await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      groupDeleted = true;

      evidence.push({
        prompt_template_id: prompt.id,
        prompt_template_name: prompt.name,
        eval_group_id: groupId,
        eval_template_id: template.id,
        prompt_eval_config_id: configId,
        required_keys: requiredKeys,
        mapping,
        active_config_count_after_apply: Number(appliedAudit.active_count),
        deleted_config_count_after_cleanup: Number(
          deletedConfigAudit.deleted_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedConfigAudit.configs, "configs")[0]
            ?.deleted_at_set === true,
      });
    },
  },
  {
    id: "EVAL-API-009",
    title: "Apply eval group to simulation run and delete generated config",
    tags: ["evals", "simulate", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_sim_apply_group_${suffix}`;
      const description =
        "Eval group simulation fanout created by API journey.";

      const { runTest, template, requiredKeys, mapping } =
        await resolveEvalGroupSimulateApplyFixtures(
          client,
          organizationId,
          workspaceId,
        );

      let groupDeleted = false;
      let configDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name: groupName,
        description,
        eval_template_ids: [template.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer(
        "delete API journey simulate apply eval group",
        async () => {
          if (groupDeleted) return null;
          return ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
            ),
          );
        },
      );

      const appliedConfigs = payloadArray(
        await client.post(apiPath("/model-hub/eval-groups/apply-eval-group/"), {
          eval_group_id: groupId,
          page_id: "SIMULATE",
          filters: {
            simulate_id: runTest.id,
            model: "turing_small",
            error_localizer: false,
            filters: [],
          },
          mapping,
          params: {},
        }),
        "configs",
      );
      const appliedConfig = appliedConfigs.find(
        (config) => config?.template_id === template.id,
      );
      assert(
        isUuid(appliedConfig?.id),
        "Simulate apply did not return a simulate eval config id.",
      );
      assertSimulateApplyPayload(appliedConfig, {
        templateId: template.id,
        mapping,
      });

      const configId = appliedConfig.id;
      cleanup.defer("delete API journey simulate eval config", async () => {
        if (configDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/",
              {
                run_test_id: runTest.id,
                eval_config_id: configId,
              },
            ),
            { okStatuses: [200, 400, 404] },
          ),
        );
      });

      const appliedAudit = await loadEvalGroupSimulateApplyDbAudit(
        groupId,
        runTest.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupSimulateApplyDbAudit(appliedAudit, {
        groupId,
        runTestId: runTest.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: false,
      });

      await client.delete(
        apiPath(
          "/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/",
          {
            run_test_id: runTest.id,
            eval_config_id: configId,
          },
        ),
      );
      configDeleted = true;

      const deletedConfigAudit = await loadEvalGroupSimulateApplyDbAudit(
        groupId,
        runTest.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupSimulateApplyDbAudit(deletedConfigAudit, {
        groupId,
        runTestId: runTest.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: true,
      });

      await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      groupDeleted = true;

      evidence.push({
        run_test_id: runTest.id,
        run_test_name: runTest.name,
        eval_group_id: groupId,
        eval_template_id: template.id,
        simulate_eval_config_id: configId,
        required_keys: requiredKeys,
        mapping,
        existing_active_eval_configs: Number(runTest.active_eval_config_count),
        active_config_count_after_apply: Number(appliedAudit.active_count),
        deleted_config_count_after_cleanup: Number(
          deletedConfigAudit.deleted_count,
        ),
        run_test_active_configs_after_cleanup: Number(
          deletedConfigAudit.run_test_active_config_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedConfigAudit.configs, "configs")[0]
            ?.deleted_at_set === true,
      });
    },
  },
  {
    id: "EVAL-API-010",
    title: "Apply eval group to experiment and delete generated metric",
    tags: ["evals", "experiments", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_experiment_apply_group_${suffix}`;
      const description =
        "Eval group experiment fanout created by API journey.";

      const { experiment, template, requiredKeys, mapping, mappedColumns } =
        await resolveEvalGroupExperimentApplyFixtures(
          client,
          organizationId,
          workspaceId,
        );

      let groupDeleted = false;
      let metricDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name: groupName,
        description,
        eval_template_ids: [template.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer(
        "delete API journey experiment apply eval group",
        async () => {
          if (groupDeleted) return null;
          return ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
            ),
          );
        },
      );

      const applyResult = await client.post(
        apiPath("/model-hub/eval-groups/apply-eval-group/"),
        {
          eval_group_id: groupId,
          page_id: "EXPERIMENT",
          filters: {
            experiment_id: experiment.id,
            model: "turing_small",
            error_localizer: false,
          },
          mapping,
          params: {},
        },
      );
      assert(
        applyResult === null || applyResult === undefined,
        "Experiment apply unexpectedly returned a non-empty payload.",
      );

      const appliedAudit = await loadEvalGroupExperimentApplyDbAudit(
        groupId,
        experiment.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupExperimentApplyDbAudit(appliedAudit, {
        groupId,
        experimentId: experiment.id,
        datasetId: experiment.dataset_id,
        organizationId,
        workspaceId,
        templateId: template.id,
        mapping,
        expectedDeleted: false,
      });

      const metric = payloadArray(appliedAudit.metrics, "metrics")[0];
      const metricId = metric?.id;
      assert(
        isUuid(metricId),
        "Experiment apply DB audit did not return metric id.",
      );
      cleanup.defer("delete API journey experiment eval metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath(
              "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
              {
                dataset_id: experiment.dataset_id,
                eval_id: metricId,
              },
            ),
            {
              body: { delete_column: true, experiment_id: experiment.id },
              okStatuses: [200, 400, 404],
            },
          ),
        );
      });

      await client.delete(
        apiPath(
          "/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/",
          {
            dataset_id: experiment.dataset_id,
            eval_id: metricId,
          },
        ),
        { body: { delete_column: true, experiment_id: experiment.id } },
      );
      metricDeleted = true;

      const deletedMetricAudit = await loadEvalGroupExperimentApplyDbAudit(
        groupId,
        experiment.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupExperimentApplyDbAudit(deletedMetricAudit, {
        groupId,
        experimentId: experiment.id,
        datasetId: experiment.dataset_id,
        organizationId,
        workspaceId,
        templateId: template.id,
        mapping,
        expectedDeleted: true,
      });

      await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      groupDeleted = true;

      evidence.push({
        experiment_id: experiment.id,
        experiment_name: experiment.name,
        dataset_id: experiment.dataset_id,
        dataset_name: experiment.dataset_name,
        eval_group_id: groupId,
        eval_template_id: template.id,
        created_metric_id: metricId,
        required_keys: requiredKeys,
        mapped_columns: mappedColumns,
        existing_active_eval_metrics: Number(
          experiment.active_eval_metric_count,
        ),
        active_metric_count_after_apply: Number(appliedAudit.active_count),
        deleted_metric_count_after_cleanup: Number(
          deletedMetricAudit.deleted_count,
        ),
        experiment_active_metrics_after_cleanup: Number(
          deletedMetricAudit.experiment_active_metric_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedMetricAudit.metrics, "metrics")[0]
            ?.deleted_at_set === true,
      });
    },
  },
  {
    id: "EVAL-API-011",
    title: "Apply eval group to eval task and delete generated custom config",
    tags: [
      "evals",
      "observe",
      "eval-task",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_eval_task_apply_group_${suffix}`;
      const description = "Eval group eval-task fanout created by API journey.";

      const { project, template, requiredKeys, mapping } =
        await resolveEvalGroupEvalTaskApplyFixtures(
          client,
          organizationId,
          workspaceId,
        );

      let groupDeleted = false;
      let configDeleted = false;
      const created = await client.post(apiPath("/model-hub/eval-groups/"), {
        name: groupName,
        description,
        eval_template_ids: [template.id],
      });
      const groupId = created?.id;
      assert(isUuid(groupId), "Eval group create did not return a UUID id.");
      cleanup.defer(
        "delete API journey eval-task apply eval group",
        async () => {
          if (groupDeleted) return null;
          return ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
            ),
          );
        },
      );

      const appliedConfigs = payloadArray(
        await client.post(apiPath("/model-hub/eval-groups/apply-eval-group/"), {
          eval_group_id: groupId,
          page_id: "EVAL_TASK",
          filters: {
            project_id: project.id,
            model: "turing_small",
            error_localizer: false,
          },
          mapping,
          params: {},
        }),
        "configs",
      );
      const appliedConfig = appliedConfigs.find(
        (config) => config?.eval_template === template.id,
      );
      assert(
        isUuid(appliedConfig?.id),
        "Eval-task apply did not return a custom eval config id.",
      );
      assertEvalTaskApplyPayload(appliedConfig, {
        templateId: template.id,
        projectId: project.id,
        mapping,
      });

      const configId = appliedConfig.id;
      cleanup.defer(
        "delete API journey eval-task custom eval config",
        async () => {
          if (configDeleted) return null;
          return ignoreNotFound(() =>
            client.delete(
              apiPath("/tracer/custom-eval-config/{id}/", {
                id: configId,
              }),
              { okStatuses: [200, 204, 400, 404] },
            ),
          );
        },
      );

      const appliedAudit = await loadEvalGroupEvalTaskApplyDbAudit(
        groupId,
        project.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupEvalTaskApplyDbAudit(appliedAudit, {
        groupId,
        projectId: project.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: false,
      });

      await client.delete(
        apiPath("/tracer/custom-eval-config/{id}/", {
          id: configId,
        }),
      );
      configDeleted = true;

      const deletedConfigAudit = await loadEvalGroupEvalTaskApplyDbAudit(
        groupId,
        project.id,
        organizationId,
        workspaceId,
      );
      assertEvalGroupEvalTaskApplyDbAudit(deletedConfigAudit, {
        groupId,
        projectId: project.id,
        organizationId,
        workspaceId,
        templateId: template.id,
        configId,
        mapping,
        expectedDeleted: true,
      });

      await client.delete(
        apiPath("/model-hub/eval-groups/{id}/", { id: groupId }),
      );
      groupDeleted = true;

      evidence.push({
        project_id: project.id,
        project_name: project.name,
        project_trace_type: project.trace_type,
        eval_group_id: groupId,
        eval_template_id: template.id,
        custom_eval_config_id: configId,
        required_keys: requiredKeys,
        mapping,
        existing_active_custom_eval_configs: Number(
          project.active_config_count,
        ),
        active_config_count_after_apply: Number(appliedAudit.active_count),
        deleted_config_count_after_cleanup: Number(
          deletedConfigAudit.deleted_count,
        ),
        project_active_configs_after_cleanup: Number(
          deletedConfigAudit.project_active_config_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedConfigAudit.configs, "configs")[0]
            ?.deleted_at_set === true,
      });
    },
  },
  {
    id: "PROMPT-API-001",
    title:
      "Prompt draft create, save, rename, commit, detail reload, and delete lifecycle",
    tags: ["prompts", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, evidence }) {
      requireMutations();
      const promptName = `api journey prompt ${runId}`;
      const updatedName = `api journey prompt updated ${runId}`;
      const updatedUserText = `Hello {{name}} from ${runId}`;

      const created = await client.post(
        apiPath("/model-hub/prompt-templates/create-draft/"),
        {
          name: promptName,
          variable_names: { name: ["Kartik"] },
          metadata: { source: "api-journey" },
          prompt_config: [promptConfig("You are concise.", "Hello {{name}}")],
        },
      );
      const promptId =
        created?.id || created?.root_template || created?.rootTemplate;
      assert(promptId, "Prompt draft create did not return prompt id.");
      cleanup.defer("delete API journey prompt", () =>
        client.post(apiPath("/model-hub/prompt-templates/bulk-delete/"), {
          ids: [promptId],
        }),
      );

      const detail = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(detail?.name === promptName, "Prompt detail returned wrong name.");
      assert(
        detail?.version === "v1",
        "Prompt detail did not expose draft v1.",
      );

      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/run_template/", {
          id: promptId,
        }),
        {
          name: promptName,
          version: "v1",
          is_run: false,
          variable_names: { name: ["Kartik"] },
          placeholders: {},
          evaluation_configs: [],
          prompt_config: [
            promptConfig("You are very concise.", updatedUserText),
          ],
        },
      );

      const saved = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(
        JSON.stringify(saved?.prompt_config || []).includes(updatedUserText),
        "Prompt draft save did not persist updated message content.",
      );

      const renamed = await client.post(
        apiPath("/model-hub/prompt-templates/{id}/save-name/", {
          id: promptId,
        }),
        { name: updatedName },
      );
      assert(
        renamed?.name === updatedName,
        "Prompt rename did not return updated name.",
      );

      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/commit/", { id: promptId }),
        {
          version_name: "v1",
          message: `API journey commit ${runId}`,
          is_draft: false,
          set_default: true,
        },
      );
      const committed = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(
        committed?.is_draft === false,
        "Prompt commit did not mark the version as non-draft on reload.",
      );

      evidence.push({
        prompt_id: promptId,
        prompt_name: updatedName,
        version: committed.version,
      });
    },
  },
  {
    id: "PROMPT-API-006",
    title:
      "Legacy prompt template list, versions, default, SDK, and cleanup lifecycle",
    tags: ["prompts", "legacy", "mutating", "data-roundtrip", "db-audit"],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const promptName = `api journey legacy prompt ${suffix}`;
      const v1Text = `Legacy v1 says hello to {{name}} from ${runId}`;
      const v2Text = `Legacy v2 says hello to {{name}} from ${runId}`;

      const created = await client.post(
        apiPath("/model-hub/prompt-templates/create-draft/"),
        {
          name: promptName,
          variable_names: { name: ["Ada", "Grace"] },
          metadata: { source: "api-journey", run_id: runId, version: "v1" },
          prompt_config: [promptConfig("You are concise.", v1Text)],
        },
      );
      const promptId =
        created?.id || created?.root_template || created?.rootTemplate;
      assert(isUuid(promptId), "Legacy prompt create did not return a UUID id.");
      cleanup.defer("delete legacy API journey prompt", () =>
        deletePromptTemplateIfPresent(client, promptId),
      );

      const listed = asArray(
        await client.get(apiPath("/model-hub/prompt-templates/"), {
          query: { search: promptName, page: 1, limit: 10 },
        }),
      );
      assert(
        listed.some((row) => row?.id === promptId),
        "Legacy prompt list/search did not include the created prompt.",
      );

      const detail = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(
        detail?.name === promptName &&
          detail?.version === "v1" &&
          JSON.stringify(detail?.prompt_config || []).includes(v1Text),
        `Legacy prompt detail did not return v1 draft state: ${JSON.stringify(
          detail,
        )}.`,
      );

      const nextVersionBeforeDraft = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/get-next-version/", {
          id: promptId,
        }),
      );
      assert(
        nextVersionBeforeDraft?.next_version === "v2",
        `Expected next prompt version v2, got ${JSON.stringify(
          nextVersionBeforeDraft,
        )}.`,
      );

      const createdDrafts = payloadArray(
        await client.post(
          apiPath("/model-hub/prompt-templates/{id}/add-new-draft/", {
            id: promptId,
          }),
          {
            new_prompts: [
              {
                variable_names: { name: ["Ada", "Grace"] },
                evaluation_configs: [],
                metadata: {
                  source: "api-journey",
                  run_id: runId,
                  version: "v2",
                },
                prompt_config: [
                  promptConfig("You are a sharper concise assistant.", v2Text),
                ],
              },
            ],
          },
        ),
        "result",
      );
      assert(
        createdDrafts.some((row) => row?.template_version === "v2"),
        `add-new-draft did not create v2: ${JSON.stringify(createdDrafts)}.`,
      );

      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/run_template/", {
          id: promptId,
        }),
        {
          name: promptName,
          version: "v2",
          is_run: false,
          variable_names: { name: ["Ada", "Grace"] },
          placeholders: {},
          evaluation_configs: [],
          prompt_config: [
            promptConfig("You are a sharper concise assistant.", v2Text),
          ],
        },
      );

      const versions = asArray(
        await client.get(
          apiPath("/model-hub/prompt-templates/{id}/versions/", {
            id: promptId,
          }),
          { query: { page: 1, limit: 10 } },
        ),
      );
      const versionNames = versions.map((row) => row?.template_version);
      assert(
        versionNames.includes("v1") && versionNames.includes("v2"),
        `Legacy prompt versions endpoint did not return v1/v2: ${JSON.stringify(
          versions,
        )}.`,
      );
      assert(
        versions.find((row) => row?.template_version === "v2")
          ?.prompt_config_snapshot?.messages?.[1]?.content?.[0]?.text === v2Text,
        "Legacy prompt versions endpoint did not return the saved v2 content.",
      );

      const comparison = await client.post(
        apiPath("/model-hub/prompt-templates/{id}/compare-versions/", {
          id: promptId,
        }),
        { versions: ["v1", "v2"] },
      );
      assert(
        payloadArray(comparison?.data, "data").length === 2 ||
          payloadArray(comparison, "data").length === 2,
        `Legacy prompt compare-versions did not return both versions: ${JSON.stringify(
          comparison,
        )}.`,
      );

      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/commit/", { id: promptId }),
        {
          version_name: "v1",
          message: `API journey v1 commit ${runId}`,
          is_draft: false,
          set_default: true,
        },
      );
      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/set_default/", {
          id: promptId,
        }),
        { version_name: "v2" },
      );
      const defaultV2 = await client.get(
        apiPath("/model-hub/prompt-templates/get-template-by-name/"),
        { query: { name: promptName } },
      );
      assert(
        defaultV2?.version === "v2" && defaultV2?.is_default === true,
        `set_default did not make v2 the unique default lookup: ${JSON.stringify(
          defaultV2,
        )}.`,
      );
      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/commit/", { id: promptId }),
        {
          version_name: "v2",
          message: `API journey v2 commit ${runId}`,
          is_draft: false,
          set_default: true,
        },
      );
      const defaultAfterCommit = await client.get(
        apiPath("/model-hub/prompt-templates/get-template-by-name/"),
        { query: { name: promptName } },
      );
      assert(
        defaultAfterCommit?.version === "v2" &&
          defaultAfterCommit?.is_default === true,
        `commit set_default did not keep v2 as default: ${JSON.stringify(
          defaultAfterCommit,
        )}.`,
      );
      const explicitV1 = await client.get(
        apiPath("/model-hub/prompt-templates/get-template-by-name/"),
        { query: { name: promptName, version: "v1" } },
      );
      assert(
        explicitV1?.version === "v1",
        "get-template-by-name with explicit version did not return v1.",
      );

      const runStatus = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/get-run-status/", {
          id: promptId,
        }),
        { query: { template_version: "v2" } },
      );
      assert(
        runStatus?.executions_result?.template_version === "v2" &&
          Array.isArray(runStatus.executions_result.output),
        `get-run-status did not return v2 status payload: ${JSON.stringify(
          runStatus,
        )}.`,
      );

      const sdkCode = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/get-sdk-code/{language}/", {
          id: promptId,
          language: "python",
        }),
      );
      assert(
        typeof sdkCode?.python === "string" &&
          sdkCode.python.includes(`/model-hub/prompt-templates/${promptId}`) &&
          sdkCode.python.includes("YOUR_API_KEY") &&
          !/[0-9a-f]{32}/i.test(sdkCode.python),
        "Legacy prompt SDK code did not return placeholder-only Python snippet.",
      );

      const activeAudit = await loadPromptTemplateVersionDbAudit({
        promptId,
        organizationId,
        workspaceId,
      });
      assertPromptTemplateVersionDbAudit(activeAudit, {
        promptId,
        organizationId,
        workspaceId,
        expectedDeleted: false,
        expectedDefaultVersion: "v2",
      });

      await deletePromptTemplateIfPresent(client, promptId);
      const deletedAudit = await loadPromptTemplateVersionDbAudit({
        promptId,
        organizationId,
        workspaceId,
      });
      assertPromptTemplateVersionDbAudit(deletedAudit, {
        promptId,
        organizationId,
        workspaceId,
        expectedDeleted: true,
        expectedDefaultVersion: "v2",
      });

      evidence.push({
        prompt_id: promptId,
        prompt_name: promptName,
        versions: versionNames,
        default_version: defaultAfterCommit.version,
        run_status_version: runStatus.executions_result.template_version,
        sdk_code_keys: Object.keys(sdkCode),
        active_default_version_count: activeAudit.active_default_version_count,
        deleted_versions_without_deleted_at_count:
          deletedAudit.deleted_versions_without_deleted_at_count,
      });
    },
  },
  {
    id: "PROMPT-API-007",
    title:
      "Prompt Workbench safe provider run preview, status polling, and DB readback",
    tags: [
      "prompts",
      "workbench",
      "provider",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const promptName = `api journey prompt run ${suffix}`;
      const variableNames = { topic: ["coverage"] };
      const runConfig = promptConfig(
        "Reply with exactly one short phrase.",
        "Say ready for {{topic}}.",
      );
      let promptDeleted = false;

      const options = await client.get(
        apiPath("/model-hub/develops/retrieve_run_prompt_options/"),
      );
      const safeModel = payloadArray(options, "models").find(
        (model) =>
          model?.model_name === "gpt-4o-mini" &&
          model?.providers === "openai" &&
          model?.mode === "chat" &&
          model?.is_available === true,
      );
      if (!safeModel) {
        skip("gpt-4o-mini is not available for safe prompt Workbench runs.");
      }
      runConfig.configuration.model = safeModel.model_name;
      runConfig.configuration.model_detail = { type: safeModel.mode };
      runConfig.configuration.max_tokens = 16;
      runConfig.configuration.temperature = 0;

      const createdPrompt = await client.post(
        apiPath("/model-hub/prompt-templates/create-draft/"),
        {
          name: promptName,
          variable_names: variableNames,
          metadata: { source: "api-journey", run_id: runId },
          prompt_config: [runConfig],
        },
      );
      const promptId =
        createdPrompt?.id ||
        createdPrompt?.root_template ||
        createdPrompt?.rootTemplate;
      assert(
        isUuid(promptId),
        "Prompt run journey create did not return a UUID id.",
      );
      cleanup.defer("delete API journey prompt run prompt", async () => {
        if (!promptDeleted) {
          await deletePromptTemplateIfPresent(client, promptId);
        }
      });

      const submitted = await client.post(
        apiPath("/model-hub/prompt-templates/{id}/run_template/", {
          id: promptId,
        }),
        {
          name: promptName,
          version: "v1",
          is_run: "prompt",
          variable_names: variableNames,
          placeholders: {},
          evaluation_configs: [],
          prompt_config: [runConfig],
        },
      );
      assert(
        submitted?.template_id === promptId,
        "Prompt Workbench run submit returned the wrong template id.",
      );

      const runStatus = await waitForPromptTemplateRunCompletion(client, {
        promptId,
        templateVersion: "v1",
      });
      assertPromptTemplateRunStatus(runStatus, {
        promptId,
        templateVersion: "v1",
        modelName: safeModel.model_name,
      });

      const activeAudit = await loadPromptRunPreviewDbAudit({
        promptId,
        templateVersion: "v1",
        organizationId,
        workspaceId,
      });
      assertPromptRunPreviewDbAudit(activeAudit, {
        promptId,
        organizationId,
        workspaceId,
        modelName: safeModel.model_name,
        expectedDeleted: false,
      });

      await deletePromptTemplateIfPresent(client, promptId);
      promptDeleted = true;
      const deletedAudit = await loadPromptRunPreviewDbAudit({
        promptId,
        templateVersion: "v1",
        organizationId,
        workspaceId,
      });
      assertPromptRunPreviewDbAudit(deletedAudit, {
        promptId,
        organizationId,
        workspaceId,
        modelName: safeModel.model_name,
        expectedDeleted: true,
      });

      evidence.push({
        prompt_id: promptId,
        prompt_name: promptName,
        model: safeModel.model_name,
        provider: safeModel.providers,
        output_count: activeAudit.output_count,
        metadata_count: activeAudit.metadata_count,
        status: runStatus.status,
        deleted_at_set: deletedAudit.prompt_deleted_at_set,
      });
    },
  },
  {
    id: "PROMPT-API-003",
    title:
      "Prompt Workbench folder list/search, prompt assignment, and soft-delete lifecycle",
    tags: [
      "prompts",
      "workbench",
      "folders",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "").slice(0, 18);
      const folderName = `api_journey_prompt_folder_${suffix}`;
      const renamedFolderName = `api_journey_renamed_folder_${suffix}`;
      const promptName = `api journey workbench prompt ${suffix}`;
      const cascadePromptName = `api journey workbench cascade ${suffix}`;
      let folderDeleted = false;
      let promptDeleted = false;
      let cascadePromptDeleted = false;

      const foldersBefore = asArray(
        await client.get(apiPath("/model-hub/prompt-folders/")),
      );

      const createdFolder = await client.post(
        apiPath("/model-hub/prompt-folders/"),
        { name: folderName },
      );
      const folderId = createdFolder?.id;
      assert(
        isUuid(folderId),
        "Prompt folder create did not return a UUID id.",
      );
      cleanup.defer("delete API journey prompt folder", async () => {
        if (!folderDeleted) {
          await ignoreNotFound(() =>
            client.delete(
              apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
            ),
          );
        }
      });

      const folderDetail = await client.get(
        apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
      );
      assert(
        folderDetail?.name === folderName,
        "Prompt folder detail name mismatch.",
      );

      const createdFolderRow = await findWorkbenchFolderRow(client, folderName);
      assert(
        createdFolderRow?.id === folderId,
        "Workbench folder search did not return the created folder.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(apiPath("/model-hub/prompt-folders/"), {
            name: folderName,
          }),
        400,
        "Duplicate prompt folder create unexpectedly succeeded.",
      );

      const renamedFolder = await client.patch(
        apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
        { name: renamedFolderName },
      );
      assert(
        renamedFolder?.name === renamedFolderName,
        "Prompt folder rename did not return the updated name.",
      );
      const renamedFolderRow = await findWorkbenchFolderRow(
        client,
        renamedFolderName,
      );
      assert(
        renamedFolderRow?.id === folderId,
        "Workbench folder search did not return the renamed folder.",
      );
      const oldFolderRows = await searchWorkbenchRows(client, folderName);
      assert(
        !oldFolderRows.some((row) => row?.id === folderId),
        "Workbench folder search still returned the old folder name.",
      );

      const promptId = await createWorkbenchPrompt(client, {
        folderId,
        name: promptName,
        runId,
        description: "Prompt Workbench API journey bulk-delete candidate.",
      });
      cleanup.defer("delete API journey workbench prompt", async () => {
        if (!promptDeleted) {
          await deletePromptTemplateIfPresent(client, promptId);
        }
      });

      const promptDetail = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(promptDetail?.name === promptName, "Prompt detail name mismatch.");
      assert(
        promptDetail?.prompt_folder === folderId,
        "Prompt detail did not preserve folder assignment.",
      );
      assert(
        JSON.stringify(promptDetail?.prompt_config || []).includes(
          "{{customer}}",
        ),
        "Prompt detail did not preserve Workbench variable content.",
      );

      const folderPrompts = asArray(
        await client.get(apiPath("/model-hub/prompt-executions/"), {
          query: {
            prompt_folder: folderId,
            page: 1,
            page_size: 25,
            ordering: "-updated_at",
          },
        }),
      );
      assert(
        folderPrompts.some((row) => row?.id === promptId),
        "Prompt execution folder list did not include the assigned prompt.",
      );

      const promptSearchRow = await findWorkbenchPromptRow(client, promptName);
      assert(
        promptSearchRow?.id === promptId,
        "Workbench prompt search did not return the created prompt.",
      );

      const pinnedRows = asArray(
        await client.get(apiPath("/model-hub/prompt-executions/"), {
          query: {
            send_all: true,
            page: 1,
            page_size: 25,
            pinned_ids: `${promptId},${folderId}`,
            sort_by: "updated_at",
            sort_order: "desc",
          },
        }),
      );
      const pinnedIndexes = [promptId, folderId].map((id) =>
        pinnedRows.findIndex((row) => row?.id === id),
      );
      assert(
        pinnedIndexes.every((index) => index >= 0 && index < 2),
        "Prompt execution pinned_ids did not pin prompt and folder to the top.",
      );

      const activeAudit = await loadPromptWorkbenchDbAudit({
        folderId,
        promptId,
        organizationId,
        workspaceId,
      });
      assertPromptWorkbenchDbAudit(activeAudit, {
        folderId,
        promptId,
        organizationId,
        workspaceId,
        expectedFolderDeleted: false,
        expectedPromptDeleted: false,
        expectedVersionsDeleted: false,
      });

      await client.post(apiPath("/model-hub/prompt-templates/bulk-delete/"), {
        ids: [promptId],
      });
      promptDeleted = true;
      const deletedPromptAudit = await loadPromptWorkbenchDbAudit({
        folderId,
        promptId,
        organizationId,
        workspaceId,
      });
      assertPromptWorkbenchDbAudit(deletedPromptAudit, {
        folderId,
        promptId,
        organizationId,
        workspaceId,
        expectedFolderDeleted: false,
        expectedPromptDeleted: true,
        expectedVersionsDeleted: true,
      });

      const cascadePromptId = await createWorkbenchPrompt(client, {
        folderId,
        name: cascadePromptName,
        runId,
        description: "Prompt Workbench API journey folder-cascade candidate.",
      });
      cleanup.defer("delete API journey folder cascade prompt", async () => {
        if (!cascadePromptDeleted) {
          await deletePromptTemplateIfPresent(client, cascadePromptId);
        }
      });

      await client.delete(
        apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
      );
      folderDeleted = true;
      cascadePromptDeleted = true;

      const deletedFolderAudit = await loadPromptWorkbenchDbAudit({
        folderId,
        promptId: cascadePromptId,
        organizationId,
        workspaceId,
      });
      assertPromptWorkbenchDbAudit(deletedFolderAudit, {
        folderId,
        promptId: cascadePromptId,
        organizationId,
        workspaceId,
        expectedFolderDeleted: true,
        expectedPromptDeleted: true,
        expectedVersionsDeleted: true,
      });

      const rowsAfterFolderDelete = await searchWorkbenchRows(
        client,
        renamedFolderName,
      );
      assert(
        !rowsAfterFolderDelete.some((row) => row?.id === folderId),
        "Deleted prompt folder still appeared in Workbench search.",
      );

      evidence.push({
        folder_id: folderId,
        folder_name: renamedFolderName,
        folders_before: foldersBefore.length,
        prompt_id: promptId,
        cascade_prompt_id: cascadePromptId,
        folder_prompt_rows: folderPrompts.length,
        pinned_ids_top_indexes: pinnedIndexes,
        prompt_deleted_at_set: deletedPromptAudit.prompt_deleted_at_set,
        prompt_versions_deleted: Number(
          deletedPromptAudit.deleted_version_count,
        ),
        folder_deleted_at_set: deletedFolderAudit.folder_deleted_at_set,
        cascade_prompt_deleted_at_set: deletedFolderAudit.prompt_deleted_at_set,
      });
    },
  },
  {
    id: "PROMPT-API-004",
    title:
      "Prompt evaluation configs, evaluation-data readback, run guards, and delete lifecycle",
    tags: [
      "prompts",
      "evaluations",
      "workbench",
      "mutating",
      "data-roundtrip",
      "db-audit",
    ],
    async run({
      client,
      cleanup,
      runId,
      evidence,
      organizationId,
      workspaceId,
    }) {
      requireMutations();
      const suffix = runId
        .replace(/[^a-z0-9]/gi, "")
        .toLowerCase()
        .slice(0, 18);
      const promptName = `api journey prompt eval ${suffix}`;
      const evalConfigName = `api_journey_prompt_eval_config_${suffix}`;
      const variableNames = {
        text: ["one two three four"],
        expected: ["a short answer"],
      };
      const promptMessages = [
        promptConfig(
          "You answer in one short sentence.",
          "Use this text: {{text}}. Expected hint: {{expected}}",
        ),
      ];
      let evalConfigDeleted = false;
      let promptDeleted = false;

      const createdPrompt = await client.post(
        apiPath("/model-hub/prompt-templates/create-draft/"),
        {
          name: promptName,
          variable_names: variableNames,
          metadata: { source: "api-journey", run_id: runId },
          prompt_config: promptMessages,
        },
      );
      const promptId =
        createdPrompt?.id ||
        createdPrompt?.root_template ||
        createdPrompt?.rootTemplate;
      assert(
        isUuid(promptId),
        "Prompt eval journey create did not return a UUID id.",
      );
      cleanup.defer("delete API journey prompt eval prompt", async () => {
        if (!promptDeleted) {
          await deletePromptTemplateIfPresent(client, promptId);
        }
      });

      const promptDetail = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/", { id: promptId }),
      );
      assert(
        promptDetail?.name === promptName,
        "Prompt eval journey detail returned wrong prompt name.",
      );
      assert(
        promptDetail?.version === "v1",
        "Prompt eval journey did not create draft v1.",
      );
      await client.post(
        apiPath("/model-hub/prompt-templates/{id}/run_template/", {
          id: promptId,
        }),
        {
          name: promptName,
          version: "v1",
          is_run: false,
          variable_names: variableNames,
          placeholders: {},
          evaluation_configs: [],
          prompt_config: promptMessages,
        },
      );

      const evalTemplate = await resolvePromptEvalTemplateForConfig(
        client,
        cleanup,
        suffix,
      );
      const mapping = buildPromptEvalConfigMapping(evalTemplate.requiredKeys);
      const createdConfig = await client.post(
        apiPath("/model-hub/prompt-templates/{id}/update-evaluation-configs/", {
          id: promptId,
        }),
        {
          id: evalTemplate.id,
          name: evalConfigName,
          mapping,
          config: { params: evalTemplate.params },
          is_run: false,
        },
      );
      const evalConfigId = createdConfig?.prompt_eval_config_id;
      assert(
        isUuid(evalConfigId),
        "Prompt eval config create did not return prompt_eval_config_id.",
      );
      cleanup.defer("delete API journey prompt eval config", async () => {
        if (!evalConfigDeleted) {
          await deletePromptEvalConfigIfPresent(client, promptId, evalConfigId);
        }
      });

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/prompt-templates/{id}/update-evaluation-configs/",
              { id: promptId },
            ),
            {
              id: evalTemplate.id,
              name: evalConfigName,
              mapping,
              config: { params: evalTemplate.params },
              is_run: false,
            },
          ),
        400,
        "Duplicate prompt eval config create unexpectedly succeeded.",
      );

      const configsPayload = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/evaluation-configs/", {
          id: promptId,
        }),
      );
      const configRows = payloadArray(
        configsPayload?.evaluation_configs,
        "evaluation_configs",
      );
      const configRow = configRows.find((row) => row?.id === evalConfigId);
      assert(
        configRow,
        "Evaluation configs did not include the created config.",
      );
      assert(
        configsPayload?.template_id === promptId,
        "Evaluation configs returned wrong prompt template id.",
      );
      assert(
        configsPayload?.template_name === promptName,
        "Evaluation configs returned wrong prompt template name.",
      );
      assert(
        configRow.eval_template_id === evalTemplate.id,
        "Prompt eval config used the wrong eval template.",
      );
      assert(
        configRow.name === evalConfigName,
        "Prompt eval config returned wrong name.",
      );
      assertPromptEvalMapping(configRow.mapping, mapping);
      assertPromptEvalRequiredKeys(
        configRow.eval_required_keys,
        evalTemplate.requiredKeys,
      );
      assertPromptEvalParams(configRow.params, evalTemplate.expectedParams);
      assertPromptEvalSchemaKeys(
        configRow.function_params_schema,
        evalTemplate.schemaKeys,
      );

      const activeAudit = await loadPromptEvalConfigDbAudit({
        promptId,
        evalConfigId,
        evalTemplateId: evalTemplate.id,
        organizationId,
        workspaceId,
      });
      assertPromptEvalConfigDbAudit(activeAudit, {
        promptId,
        evalConfigId,
        evalTemplateId: evalTemplate.id,
        organizationId,
        workspaceId,
        mapping,
        expectedDeleted: false,
      });

      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/prompt-templates/{id}/evaluations/", {
              id: promptId,
            }),
            { query: { show_var: true } },
          ),
        400,
        "Prompt evaluations without versions unexpectedly succeeded.",
      );
      await expectApiErrorStatus(
        () =>
          client.get(
            apiPath("/model-hub/prompt-templates/{id}/evaluations/", {
              id: promptId,
            }),
            { query: { versions: JSON.stringify(["v1", "v2"]) } },
          ),
        400,
        "Prompt evaluations compare=false multi-version guard did not fire.",
      );

      const evaluationData = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/evaluations/", {
          id: promptId,
        }),
        {
          query: {
            versions: JSON.stringify(["v1"]),
            show_var: true,
            show_prompts: true,
          },
        },
      );
      const versionData = evaluationData?.v1;
      assert(versionData, "Prompt evaluations did not return v1 data.");
      evidence.push({
        evaluation_data_keys: Object.keys(evaluationData),
        evaluation_variables: evaluationData.variables,
      });
      const textVariable = evaluationData.variables?.text;
      assert(
        (Array.isArray(textVariable) ? textVariable[0] : textVariable) ===
          "one two three four",
        "Prompt evaluations did not include prompt variables.",
      );
      const evalNames = payloadArray(versionData.eval_names, "eval_names");
      assert(
        evalNames.some((row) => row?.id === evalConfigId),
        "Prompt evaluations did not include the created eval config in eval_names.",
      );
      assert(
        Object.prototype.hasOwnProperty.call(
          versionData.eval_status || {},
          evalConfigId,
        ),
        "Prompt evaluations did not include eval_status for the created config.",
      );
      assert(
        Array.isArray((versionData.eval_output || {})[evalConfigId]),
        "Prompt evaluations did not return an eval_output array for the config.",
      );
      assert(
        Array.isArray(versionData.messages),
        "Prompt evaluations show_prompts did not return prompt messages.",
      );

      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
              { id: promptId },
            ),
            { version_to_run: ["v1"], prompt_eval_config_ids: [] },
          ),
        400,
        "Prompt run-evals accepted an empty eval config id list.",
      );
      await expectApiErrorStatus(
        () =>
          client.post(
            apiPath(
              "/model-hub/prompt-templates/{id}/run-evals-on-multiple-versions/",
              { id: promptId },
            ),
            { version_to_run: ["v1"], prompt_eval_config_ids: [randomUUID()] },
          ),
        400,
        "Prompt run-evals accepted an eval config id outside this prompt.",
      );

      await client.delete(
        apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
          id: promptId,
        }),
        { query: { id: evalConfigId } },
      );
      evalConfigDeleted = true;

      const deletedConfigsPayload = await client.get(
        apiPath("/model-hub/prompt-templates/{id}/evaluation-configs/", {
          id: promptId,
        }),
      );
      assert(
        !payloadArray(
          deletedConfigsPayload?.evaluation_configs,
          "evaluation_configs",
        ).some((row) => row?.id === evalConfigId),
        "Deleted prompt eval config still appeared in evaluation-configs.",
      );
      const deletedAudit = await loadPromptEvalConfigDbAudit({
        promptId,
        evalConfigId,
        evalTemplateId: evalTemplate.id,
        organizationId,
        workspaceId,
      });
      assertPromptEvalConfigDbAudit(deletedAudit, {
        promptId,
        evalConfigId,
        evalTemplateId: evalTemplate.id,
        organizationId,
        workspaceId,
        mapping,
        expectedDeleted: true,
      });

      await deletePromptTemplateIfPresent(client, promptId);
      promptDeleted = true;

      evidence.push({
        prompt_id: promptId,
        eval_template_id: evalTemplate.id,
        eval_template_name: evalTemplate.name,
        eval_template_source: evalTemplate.source,
        eval_config_id: evalConfigId,
        eval_required_keys: evalTemplate.requiredKeys,
        eval_params: configRow.params,
        eval_status: versionData.eval_status?.[evalConfigId],
        eval_config_deleted_at_set: deletedAudit.deleted_at_set,
      });
    },
  },
  {
    id: "SCORE-API-001",
    title:
      "Direct score API create, upsert update, for-source reload, and delete lifecycle",
    tags: ["scores", "annotation", "mutating", "data-roundtrip"],
    async run({ client, cleanup, runId, user, evidence }) {
      requireMutations();
      const queue = await resolveQueue(client, evidence);
      const item = await resolveQueueItem(client, queue.id, evidence, {
        status: ["pending", "in_progress", "skipped", "completed"],
      });
      const detail = await client.get(
        queuePath(
          "/model-hub/annotation-queues/{queue_id}/items/{id}/annotate-detail/",
          queue.id,
          { id: item.id },
        ),
        {
          query: {
            include_completed: true,
            include_all_annotations: true,
          },
        },
      );
      const source = resolveQueueItemSource(item, detail);
      if (!source.sourceId) {
        skip(`Could not resolve source id for queue item ${item.id}.`);
      }

      const labelName = `api journey direct score ${runId}`;
      const label = await client.post(
        apiPath("/model-hub/annotations-labels/"),
        {
          name: labelName,
          type: "text",
          description: "Temporary label for direct score API journey.",
          settings: {
            placeholder: "Score API journey",
            min_length: 0,
            max_length: 500,
          },
          allow_notes: true,
        },
      );
      const createdLabel = label?.id
        ? label
        : await findAnnotationLabelByName(client, labelName);
      const labelId = createdLabel?.id;
      assert(labelId, "Temporary score label create did not return id.");
      cleanup.defer("delete direct score label", () =>
        ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/annotations-labels/{id}/", { id: labelId }),
          ),
        ),
      );

      const firstValue = `score value ${runId}`;
      const created = await client.post(apiPath("/model-hub/scores/"), {
        source_type: source.sourceType,
        source_id: source.sourceId,
        label_id: labelId,
        value: firstValue,
        notes: `score note ${runId}`,
        queue_item_id: item.id,
      });
      assert(created?.id, "Direct score create did not return id.");
      cleanup.defer("delete direct score", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/scores/{id}/", { id: created.id })),
        ),
      );

      const updatedValue = `score value updated ${runId}`;
      const updated = await client.post(apiPath("/model-hub/scores/"), {
        source_type: source.sourceType,
        source_id: source.sourceId,
        label_id: labelId,
        value: updatedValue,
        notes: `score note updated ${runId}`,
        queue_item_id: item.id,
      });
      assert(
        updated?.id === created.id,
        "Direct score upsert created a second score instead of updating the existing score.",
      );

      const sourceScores = asArray(
        await client.get(apiPath("/model-hub/scores/for-source/"), {
          query: {
            source_type: source.sourceType,
            source_id: source.sourceId,
          },
        }),
      );
      const reloadedScore = sourceScores.find(
        (score) => score.id === created.id,
      );
      assert(
        reloadedScore,
        "Direct score was not visible in for-source reload.",
      );
      assert(
        reloadedScore.value === updatedValue,
        "Direct score updated value did not round-trip through for-source.",
      );
      assert(
        reloadedScore.notes === `score note updated ${runId}`,
        "Direct score updated notes did not round-trip through for-source.",
      );
      const email = currentUserEmail(user);
      if (email) {
        assert(
          reloadedScore.annotator_email === email,
          "Direct score did not include the current annotator email.",
        );
      }

      await client.delete(
        apiPath("/model-hub/scores/{id}/", { id: created.id }),
      );
      const afterDelete = asArray(
        await client.get(apiPath("/model-hub/scores/for-source/"), {
          query: {
            source_type: source.sourceType,
            source_id: source.sourceId,
          },
        }),
      );
      assert(
        !afterDelete.some((score) => score.id === created.id),
        "Deleted direct score was still visible in for-source reload.",
      );

      evidence.push({
        queue_id: queue.id,
        item_id: item.id,
        score_id: created.id,
        source_type: source.sourceType,
        source_id: source.sourceId,
      });
    },
  },
];

async function selectExperimentForReadCoverage(client) {
  const listRows = asArray(
    await client.get(apiPath("/model-hub/experiments/v2/list/"), {
      query: { page: 1, page_size: 20 },
    }),
  );
  if (!listRows.length) {
    skip("No experiments found for read-surface coverage.");
  }

  const candidates = [
    ...listRows.filter((row) => Number(row.eval_templates_count) > 0),
    ...listRows.filter((row) => Number(row.eval_templates_count) <= 0),
  ];
  const seen = new Set();
  for (const row of candidates) {
    if (!isUuid(row?.id) || seen.has(row.id)) continue;
    seen.add(row.id);
    const detail = await client.get(
      apiPath("/model-hub/experiments/v2/{experiment_id}/", {
        experiment_id: row.id,
      }),
    );
    if (isUuid(detail?.snapshot_dataset_id)) {
      return { listRows, row, detail };
    }
  }

  skip("No V2 experiment with a snapshot dataset found for read coverage.");
}

function assertExperimentListRow(row) {
  assert(isUuid(row.id), "Experiment list row did not expose a UUID id.");
  assert(
    typeof row.name === "string" && row.name.length > 0,
    "Experiment list row did not expose a name.",
  );
  assert(
    typeof row.status === "string" && row.status.length > 0,
    "Experiment list row did not expose a status.",
  );
  assert(
    isUuid(row.dataset),
    "Experiment list row did not expose a dataset UUID.",
  );
  assert(
    Number.isInteger(Number(row.eval_templates_count)),
    "Experiment list row did not expose eval_templates_count.",
  );
}

function assertExperimentRowsPayload(payload, dbAudit) {
  assert(
    Array.isArray(payload?.column_config),
    "Experiment rows endpoint did not return column_config.",
  );
  assert(
    Array.isArray(payload?.table),
    "Experiment rows endpoint did not return table rows.",
  );
  assert(
    payload.metadata && typeof payload.metadata === "object",
    "Experiment rows endpoint did not return metadata.",
  );
  assert(
    payload.metadata.dataset === dbAudit.snapshot_dataset_id,
    "Experiment rows metadata dataset did not match snapshot dataset.",
  );
  assert(
    Number(payload.metadata.total_rows) === Number(dbAudit.snapshot_row_count),
    "Experiment rows total did not match snapshot DB row count.",
  );
  assert(
    payload.column_config.length > 0,
    "Experiment rows endpoint returned no column config.",
  );
}

function assertExperimentColumnOnlyPayload(payload, dbAudit) {
  assert(
    Array.isArray(payload?.column_config),
    "Experiment column-only endpoint did not return column_config.",
  );
  assert(
    payload.column_config.length > 0,
    "Experiment column-only endpoint returned no column config.",
  );
  assert(
    payload.column_config.length <= Number(dbAudit.snapshot_column_count),
    "Experiment column config exceeded snapshot DB column count.",
  );
}

function assertExperimentRowDetail(payload, rowId, label) {
  const detailRows = payloadArray(payload?.table, "table");
  assert(
    detailRows.length === 1 && detailRows[0]?.row_id === rowId,
    `Experiment ${label} row detail did not return the requested row.`,
  );
}

function assertExperimentStatsPayload(payload) {
  assert(
    Array.isArray(payload?.column_config),
    "Experiment stats endpoint did not return column_config.",
  );
  assert(
    Array.isArray(payload?.table_data),
    "Experiment stats endpoint did not return table_data.",
  );
  assert(
    payload.metadata && typeof payload.metadata === "object",
    "Experiment stats endpoint did not return metadata.",
  );
}

function assertExperimentComparisonsPayload(payload, experimentId) {
  assert(
    payload?.experiment_id === experimentId,
    "Experiment comparisons endpoint returned the wrong experiment id.",
  );
  assert(
    Array.isArray(payload.comparisons),
    "Experiment comparisons endpoint did not return comparisons.",
  );
  assert(
    Number.isInteger(Number(payload.total_comparisons)),
    "Experiment comparisons endpoint did not return total_comparisons.",
  );
}

function assertExperimentComparePostPayload(payload, experimentId, label) {
  assert(
    payload?.experiment_id === experimentId,
    `Experiment ${label} compare returned the wrong experiment id.`,
  );
  assert(
    Number.isInteger(Number(payload.total_datasets)),
    `Experiment ${label} compare did not return total_datasets.`,
  );
  assert(
    Array.isArray(payload.dataset_comparisons),
    `Experiment ${label} compare did not return dataset_comparisons.`,
  );
}

function assertExperimentEvalStatsPayload(payload, experimentId, evalMetricId) {
  assert(
    payload?.experiment_id === experimentId,
    "Experiment eval-stats endpoint returned the wrong experiment id.",
  );
  assert(
    payload.evaluation_id === evalMetricId,
    "Experiment eval-stats endpoint returned the wrong eval metric id.",
  );
  assert(
    Array.isArray(payload.evaluation_columns),
    "Experiment eval-stats endpoint did not return evaluation_columns.",
  );
  assert(
    payload.evaluation_columns.length > 0,
    "Experiment eval-stats endpoint returned no evaluation columns.",
  );
}

function assertExperimentFeedbackTemplatePayload(payload) {
  assert(
    typeof payload?.eval_name === "string" && payload.eval_name.length > 0,
    "Experiment feedback template did not return eval_name.",
  );
  assert(
    typeof payload.user_eval_name === "string" &&
      payload.user_eval_name.length > 0,
    "Experiment feedback template did not return user_eval_name.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "output_type"),
    "Experiment feedback template did not return output_type.",
  );
}

function assertExperimentFeedbackCandidate(
  candidate,
  { experimentId, organizationId, workspaceId },
) {
  assert(
    candidate?.experiment_id === experimentId,
    "Experiment feedback candidate returned a different experiment.",
  );
  assert(
    candidate.organization_id === organizationId,
    "Experiment feedback candidate organization mismatch.",
  );
  if (workspaceId) {
    assert(
      candidate.workspace_id === workspaceId,
      "Experiment feedback candidate workspace mismatch.",
    );
  }
  for (const key of ["row_id", "eval_column_id", "user_eval_metric_id"]) {
    assert(
      isUuid(candidate[key]),
      `Experiment feedback candidate missing ${key}.`,
    );
  }
  assert(
    typeof candidate.feedback_value === "string" &&
      candidate.feedback_value.length > 0,
    "Experiment feedback candidate missing feedback value.",
  );
}

function assertExperimentFeedbackDbAudit(
  audit,
  {
    feedbackId,
    experimentId,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedActionType,
  },
) {
  assert(audit?.feedback_id === feedbackId, "Feedback DB audit id mismatch.");
  assert(
    audit.experiment_id === experimentId,
    "Feedback DB audit experiment mismatch.",
  );
  assert(
    audit.organization_id === organizationId,
    "Feedback DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Feedback DB audit workspace mismatch.",
    );
  }
  assert(
    audit.feedback_source === "experiment",
    "Feedback DB audit source was not experiment.",
  );
  assert(
    audit.source_column_in_snapshot === true,
    "Feedback DB audit source column was outside the experiment snapshot.",
  );
  assert(
    audit.source_column_matches_metric === true,
    "Feedback DB audit source column did not match the eval metric.",
  );
  assert(
    audit.row_in_snapshot === true,
    "Feedback DB audit row was outside the experiment snapshot.",
  );
  assert(
    audit.feedback_deleted === expectedDeleted,
    "Feedback DB audit deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      audit.feedback_deleted_at_set === true,
      "Deleted feedback missing deleted_at.",
    );
  }
  if (expectedActionType === null) {
    assert(
      audit.action_type === null,
      "Feedback action_type was set before submit.",
    );
  } else {
    assert(
      audit.action_type === expectedActionType,
      "Feedback action_type did not match submitted action.",
    );
  }
}

function firstExperimentEvalMetricId(detail, dbAudit) {
  const detailMetrics = payloadArray(
    detail?.user_eval_metrics,
    "user_eval_metrics",
  );
  for (const metric of detailMetrics) {
    if (isUuid(metric)) return metric;
    if (isUuid(metric?.id)) return metric.id;
  }
  const auditMetrics = payloadArray(
    dbAudit?.eval_metric_ids,
    "eval_metric_ids",
  );
  return auditMetrics.find((id) => isUuid(id)) || null;
}

function assertEvalSummaryTemplateList(payload, templateId) {
  const templates = payloadArray(payload?.templates, "templates");
  assert(
    Array.isArray(templates),
    "Eval summary template list did not return templates array.",
  );
  if (templateId) {
    assert(
      templates.some((template) => template.id === templateId),
      "Eval summary template list did not include created template.",
    );
  }
}

function assertEvalSummaryTemplatePayload(
  payload,
  { templateId, name, description, criteria },
) {
  assert(
    payload?.id === templateId,
    "Eval summary template returned wrong id.",
  );
  assert(payload.name === name, "Eval summary template returned wrong name.");
  assert(
    payload.description === description,
    "Eval summary template returned wrong description.",
  );
  assert(
    payload.criteria === criteria,
    "Eval summary template returned wrong criteria.",
  );
}

function assertEvalSummaryTemplateDbAudit(
  audit,
  { templateId, organizationId, name, description, criteria, expectedExists },
) {
  assert(
    audit.template_id === templateId,
    "Eval summary template DB audit returned wrong id.",
  );
  assert(
    audit.row_exists === expectedExists,
    "Eval summary template DB audit row existence did not match expectation.",
  );
  if (!expectedExists) return;
  assert(
    audit.organization_id === organizationId,
    "Eval summary template DB audit organization did not match request context.",
  );
  assert(audit.name === name, "Eval summary template DB audit name mismatch.");
  assert(
    audit.description === description,
    "Eval summary template DB audit description mismatch.",
  );
  assert(
    audit.criteria === criteria,
    "Eval summary template DB audit criteria mismatch.",
  );
}

function assertEvalGroupCreatePayload(payload, { groupId, name, description }) {
  assert(payload?.id === groupId, "Eval group create returned wrong id.");
  assert(payload.name === name, "Eval group create returned wrong name.");
  assert(
    payload.description === description,
    "Eval group create returned wrong description.",
  );
  assert(
    Array.isArray(payload.required_keys),
    "Eval group create did not return required_keys array.",
  );
}

function assertEvalGroupListPayload(payload, groupId, expectedCount) {
  const groups = payloadArray(payload?.data, "data");
  const group = groups.find((item) => item.id === groupId);
  assert(group, "Eval group list did not include created group.");
  assert(
    Number(group.evals_count) === expectedCount,
    "Eval group list returned wrong evals_count.",
  );
}

function assertEvalGroupDetailPayload(
  payload,
  { groupId, name, description, expectedTemplateIds },
) {
  assert(
    payload?.eval_group?.id === groupId,
    "Eval group detail returned wrong group id.",
  );
  assert(
    payload.eval_group.name === name,
    "Eval group detail returned wrong group name.",
  );
  assert(
    payload.eval_group.description === description,
    "Eval group detail returned wrong group description.",
  );
  const memberIds = payloadArray(payload?.members, "members")
    .map((member) => member.eval_template_id)
    .sort();
  assert(
    JSON.stringify(memberIds) ===
      JSON.stringify([...expectedTemplateIds].sort()),
    "Eval group detail returned wrong member templates.",
  );
  assert(
    Array.isArray(payload.required_keys),
    "Eval group detail did not return required_keys array.",
  );
  assert(
    payload.function_params_requirements &&
      typeof payload.function_params_requirements === "object",
    "Eval group detail did not return function_params_requirements object.",
  );
}

function assertEvalGroupDbAudit(
  audit,
  {
    groupId,
    organizationId,
    workspaceId,
    name,
    description,
    expectedTemplateIds,
    expectedDeleted,
    expectedAddHistory,
    expectedDeleteHistory,
  },
) {
  assert(audit.group_id === groupId, "Eval group DB audit returned wrong id.");
  assert(
    audit.row_exists === true,
    "Eval group DB audit did not find group row.",
  );
  assert(
    audit.organization_id === organizationId,
    "Eval group DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Eval group DB audit workspace did not match request context.",
  );
  assert(audit.name === name, "Eval group DB audit name mismatch.");
  assert(
    audit.description === description,
    "Eval group DB audit description mismatch.",
  );
  assert(
    audit.deleted === expectedDeleted,
    "Eval group DB audit deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Eval group delete did not set deleted_at.",
    );
  }
  const relationIds = payloadArray(
    audit.relationship_template_ids,
    "relationship_template_ids",
  ).sort();
  assert(
    JSON.stringify(relationIds) ===
      JSON.stringify([...expectedTemplateIds].sort()),
    "Eval group DB audit relationship template ids mismatch.",
  );
  assert(
    Number(audit.relationship_count) === expectedTemplateIds.length,
    "Eval group DB audit relationship count mismatch.",
  );
  assert(
    Number(audit.add_history_count) === expectedAddHistory,
    "Eval group DB audit ADD history count mismatch.",
  );
  assert(
    Number(audit.delete_history_count) === expectedDeleteHistory,
    "Eval group DB audit DELETE history count mismatch.",
  );
}

async function resolveEvalGroupDatasetApplyFixtures(client) {
  const datasets = asArray(
    await client.get(apiPath("/model-hub/develops/get-datasets/"), {
      query: { page: 0, page_size: 25 },
    }),
  ).filter((dataset) => isUuid(dataset?.id));
  if (!datasets.length)
    skip("No datasets found for eval group apply coverage.");

  const templates = await loadEvalTemplatesWithRequiredKeys(client);

  for (const dataset of datasets) {
    const columnsPayload = await client.get(
      apiPath("/model-hub/dataset/columns/{dataset_id}/", {
        dataset_id: dataset.id,
      }),
    );
    const columns = payloadArray(columnsPayload, "columns").filter(
      (column) => isUuid(column?.id) && typeof column?.name === "string",
    );
    const columnsByName = new Map(
      columns.map((column) => [column.name, column]),
    );
    for (const template of templates) {
      if (!template.requiredKeys.every((key) => columnsByName.has(key))) {
        continue;
      }
      return {
        dataset,
        columnsByName,
        template,
        requiredKeys: template.requiredKeys,
        mapping: Object.fromEntries(
          template.requiredKeys.map((key) => [key, columnsByName.get(key).id]),
        ),
      };
    }
  }

  skip(
    "No dataset had columns matching an eval template's required_keys for dataset apply coverage.",
  );
}

async function resolveEvalGroupPromptApplyFixtures(client) {
  const prompts = asArray(
    await client.get(apiPath("/model-hub/prompt-templates/"), {
      query: { page: 1, page_size: 25 },
    }),
  ).filter((prompt) => isUuid(prompt?.id));
  if (!prompts.length)
    skip("No prompt templates found for eval group prompt apply coverage.");

  const templates = await loadEvalTemplatesWithRequiredKeys(client);
  const template =
    templates.find(
      (candidate) =>
        candidate.requiredKeys.includes("input") &&
        candidate.requiredKeys.includes("output"),
    ) || templates[0];

  return {
    prompt: prompts[0],
    template,
    requiredKeys: template.requiredKeys,
    mapping: Object.fromEntries(template.requiredKeys.map((key) => [key, key])),
  };
}

async function resolveEvalGroupSimulateApplyFixtures(
  client,
  organizationId,
  workspaceId,
) {
  const candidateAudit = await loadSimulateRunTestApplyCandidatesDbAudit(
    organizationId,
    workspaceId,
  );
  const candidates = payloadArray(candidateAudit.candidates, "candidates");
  const runTest = candidates.find((candidate) => isUuid(candidate?.id));
  if (!runTest) {
    skip(
      "No simulation run test with an existing active eval config was found for apply coverage.",
    );
  }

  const detail = await client.get(
    apiPath("/simulate/run-tests/{run_test_id}/", {
      run_test_id: runTest.id,
    }),
  );
  assert(
    detail?.id === runTest.id,
    "Simulation run-test detail returned wrong id.",
  );
  const templates = await loadEvalTemplatesWithRequiredKeys(client);
  const template =
    templates.find(
      (candidate) =>
        candidate.requiredKeys.includes("input") &&
        candidate.requiredKeys.includes("output"),
    ) || templates[0];

  return {
    runTest: {
      ...runTest,
      name: detail.name || runTest.name,
    },
    template,
    requiredKeys: template.requiredKeys,
    mapping: Object.fromEntries(template.requiredKeys.map((key) => [key, key])),
  };
}

async function resolveEvalGroupExperimentApplyFixtures(
  client,
  organizationId,
  workspaceId,
) {
  const candidateAudit = await loadExperimentApplyCandidatesDbAudit(
    organizationId,
    workspaceId,
  );
  const candidates = payloadArray(candidateAudit.candidates, "candidates");
  if (!candidates.length) {
    skip(
      "No experiment with existing active eval metrics and source columns was found for apply coverage.",
    );
  }

  const templates = await loadEvalTemplatesWithRequiredKeys(client);
  const template =
    templates.find(
      (candidate) =>
        candidate.requiredKeys.includes("input") &&
        candidate.requiredKeys.includes("output"),
    ) || templates[0];

  for (const candidate of candidates) {
    if (!isUuid(candidate?.id) || !isUuid(candidate?.dataset_id)) continue;
    const detail = await client.get(
      apiPath("/model-hub/experiments/v2/{experiment_id}/", {
        experiment_id: candidate.id,
      }),
    );
    assert(detail?.id === candidate.id, "Experiment detail returned wrong id.");

    const columns = payloadArray(candidate.columns, "columns").filter(
      (column) => isUuid(column?.id) && typeof column?.name === "string",
    );
    if (!columns.length) continue;

    const { mapping, mappedColumns } = chooseExperimentApplyMapping(
      template.requiredKeys,
      columns,
    );
    return {
      experiment: {
        ...candidate,
        name: detail.name || candidate.name,
        dataset_id: detail.dataset_id || candidate.dataset_id,
        dataset_name: candidate.dataset_name,
      },
      template,
      requiredKeys: template.requiredKeys,
      mapping,
      mappedColumns,
    };
  }

  skip("No experiment candidate could be mapped to an eval template.");
}

async function resolveCompositeExperimentBindingFixtures(
  client,
  organizationId,
  workspaceId,
) {
  const candidateAudit = await loadExperimentApplyCandidatesDbAudit(
    organizationId,
    workspaceId,
  );
  const candidates = payloadArray(candidateAudit.candidates, "candidates");
  if (!candidates.length) {
    skip(
      "No experiment with existing active eval metrics and source columns was found for composite binding coverage.",
    );
  }

  for (const candidate of candidates) {
    if (!isUuid(candidate?.id) || !isUuid(candidate?.dataset_id)) continue;
    const detail = await client.get(
      apiPath("/model-hub/experiments/v2/{experiment_id}/", {
        experiment_id: candidate.id,
      }),
    );
    assert(detail?.id === candidate.id, "Experiment detail returned wrong id.");

    const columns = payloadArray(candidate.columns, "columns").filter(
      (column) => isUuid(column?.id) && typeof column?.name === "string",
    );
    if (!columns.length) continue;

    const { mapping, mappedColumns } = chooseExperimentApplyMapping(
      ["output", "expected"],
      columns,
    );
    if (!mapping.output || !mapping.expected) continue;

    return {
      experiment: {
        ...candidate,
        name: detail.name || candidate.name,
        dataset_id: detail.dataset_id || candidate.dataset_id,
        dataset_name: candidate.dataset_name,
      },
      requiredKeys: ["output", "expected"],
      mapping,
      mappedColumns,
    };
  }

  skip(
    "No experiment candidate could be mapped to composite output/expected keys.",
  );
}

async function resolveEvalGroupEvalTaskApplyFixtures(
  client,
  organizationId,
  workspaceId,
) {
  const candidateAudit = await loadEvalTaskProjectApplyCandidatesDbAudit(
    organizationId,
    workspaceId,
  );
  const candidates = payloadArray(candidateAudit.candidates, "candidates");
  const project = candidates.find((candidate) => isUuid(candidate?.id));
  if (!project) {
    skip("No observe project was found for eval-task apply coverage.");
  }

  const templates = await loadEvalTemplatesWithRequiredKeys(client);
  const template =
    templates.find(
      (candidate) =>
        candidate.requiredKeys.includes("input") &&
        candidate.requiredKeys.includes("output"),
    ) || templates[0];

  return {
    project,
    template,
    requiredKeys: template.requiredKeys,
    mapping: Object.fromEntries(template.requiredKeys.map((key) => [key, key])),
  };
}

function chooseExperimentApplyMapping(requiredKeys, columns) {
  const byName = new Map(columns.map((column) => [column.name, column]));
  const usedColumnIds = new Set();
  const preferredNamesByKey = {
    input: ["input", "sales_conversation", "customer_profile", "scenario_id"],
    output: ["output", "agent_response", "sales", "outcome"],
    hypothesis: ["hypothesis", "agent_response", "sales_conversation"],
    reference: ["reference", "ground_truth", "outcome", "customer_concern"],
    expected: ["expected", "ground_truth", "outcome"],
    text: ["text", "sales_conversation", "customer_profile"],
    context: ["context", "customer_profile", "customer_concern"],
  };
  const mapping = {};
  const mappedColumns = {};

  for (const key of requiredKeys) {
    const preferredNames = preferredNamesByKey[key] || [key];
    let selected = preferredNames.map((name) => byName.get(name)).find(Boolean);
    if (!selected || usedColumnIds.has(selected.id)) {
      selected =
        columns.find((column) => !usedColumnIds.has(column.id)) || columns[0];
    }
    assert(selected, `No experiment column available for required key ${key}.`);
    mapping[key] = selected.id;
    mappedColumns[key] = selected.name;
    usedColumnIds.add(selected.id);
  }

  return { mapping, mappedColumns };
}

async function loadEvalTemplatesWithRequiredKeys(client) {
  const templatesPayload = await client.post(
    apiPath("/model-hub/eval-templates/list/"),
    {
      page: 0,
      page_size: 30,
      owner_filter: "all",
      sort_by: "updated_at",
      sort_order: "desc",
    },
  );
  const templateItems = payloadArray(templatesPayload?.items, "items").filter(
    (template) => isUuid(template?.id),
  );
  if (!templateItems.length)
    skip("No eval templates found for eval group apply coverage.");

  const templates = [];
  for (const item of templateItems) {
    const detail = await client.get(
      apiPath("/model-hub/eval-templates/{template_id}/detail/", {
        template_id: item.id,
      }),
    );
    const requiredKeys = payloadArray(
      detail?.required_keys ||
        detail?.eval_required_keys ||
        detail?.config?.required_keys,
      "required_keys",
    ).filter((key) => typeof key === "string" && key.length > 0);
    if (!requiredKeys.length) continue;
    templates.push({
      ...item,
      ...detail,
      id: item.id,
      requiredKeys: [...new Set(requiredKeys)],
    });
  }
  if (!templates.length)
    skip("No eval templates with required_keys found for apply coverage.");
  return templates;
}

function assertPromptApplyPayload(payload, { templateId, mapping }) {
  assert(
    payload?.eval_template_id === templateId,
    "Prompt apply returned wrong eval template id.",
  );
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      payload.mapping?.[key] === value,
      `Prompt apply payload did not preserve mapping for ${key}.`,
    );
  }
}

function assertSimulateApplyPayload(payload, { templateId, mapping }) {
  assert(
    payload?.template_id === templateId,
    "Simulate apply returned wrong eval template id.",
  );
  assert(
    payload.status === "Completed",
    "Simulate apply returned unexpected default status.",
  );
  assert(
    payload.model === "turing_small",
    "Simulate apply did not return selected model.",
  );
  assert(
    payload.error_localizer === false,
    "Simulate apply did not return error_localizer=false.",
  );
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      payload.mapping?.[key] === value,
      `Simulate apply payload did not preserve mapping for ${key}.`,
    );
  }
}

function assertEvalTaskApplyPayload(
  payload,
  { templateId, projectId, mapping },
) {
  assert(
    payload?.eval_template === templateId,
    "Eval-task apply returned wrong eval template id.",
  );
  assert(
    payload.project === projectId,
    "Eval-task apply returned wrong project id.",
  );
  assert(
    payload.model === "turing_small",
    "Eval-task apply did not return selected model.",
  );
  assert(
    payload.error_localizer === false,
    "Eval-task apply did not return error_localizer=false.",
  );
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      payload.mapping?.[key] === value,
      `Eval-task apply payload did not preserve mapping for ${key}.`,
    );
  }
}

function assertEvalGroupDatasetApplyDbAudit(
  audit,
  {
    groupId,
    datasetId,
    organizationId,
    workspaceId,
    templateId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit.group_id === groupId,
    "Dataset apply DB audit group id mismatch.",
  );
  assert(
    audit.dataset_id === datasetId,
    "Dataset apply DB audit dataset id mismatch.",
  );
  assert(
    Number(audit.metric_count) === 1,
    "Dataset apply should create exactly one metric for the temporary group.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Dataset apply DB audit active metric count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Dataset apply DB audit deleted metric count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Dataset apply cleanup left a deleted metric without deleted_at.",
  );

  const metrics = payloadArray(audit.metrics, "metrics");
  const metric = metrics[0];
  assert(
    metric?.template_id === templateId,
    "Dataset apply used wrong template.",
  );
  assert(
    metric.organization_id === organizationId,
    "Dataset apply metric organization did not match request context.",
  );
  assert(
    !workspaceId || metric.workspace_id === workspaceId,
    "Dataset apply metric workspace did not match request context.",
  );
  assert(
    metric.deleted === expectedDeleted,
    "Dataset apply metric deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      metric.deleted_at_set === true,
      "Dataset apply metric delete did not set deleted_at.",
    );
  } else {
    assert(
      metric.status === "Inactive",
      "Dataset apply metric was not inactive.",
    );
    assert(
      metric.show_in_sidebar === true,
      "Dataset apply metric was hidden from sidebar unexpectedly.",
    );
    assert(
      metric.model === "turing_small",
      "Dataset apply metric did not persist selected model.",
    );
    assert(
      metric.error_localizer === false,
      "Dataset apply metric did not persist error_localizer=false.",
    );
  }

  const configMapping = metric.config?.mapping || {};
  for (const [key, columnId] of Object.entries(mapping)) {
    assert(
      configMapping[key] === columnId,
      `Dataset apply metric config did not preserve mapping for ${key}.`,
    );
  }
  assert(
    metric.config?.reason_column === true,
    "Dataset apply metric config did not enable reason_column.",
  );
}

function assertEvalGroupPromptApplyDbAudit(
  audit,
  {
    groupId,
    promptId,
    organizationId,
    workspaceId,
    templateId,
    configId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit.group_id === groupId,
    "Prompt apply DB audit group id mismatch.",
  );
  assert(
    audit.prompt_template_id === promptId,
    "Prompt apply DB audit prompt id mismatch.",
  );
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt apply DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.prompt_workspace_id === workspaceId,
    "Prompt apply DB audit workspace did not match request context.",
  );
  assert(
    Number(audit.config_count) === 1,
    "Prompt apply should create exactly one config for the temporary group.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Prompt apply DB audit active config count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Prompt apply DB audit deleted config count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Prompt apply cleanup left a deleted config without deleted_at.",
  );

  const configs = payloadArray(audit.configs, "configs");
  const config = configs[0];
  assert(config?.id === configId, "Prompt apply DB audit config id mismatch.");
  assert(
    config.eval_template_id === templateId,
    "Prompt apply DB audit used wrong template.",
  );
  assert(
    config.deleted === expectedDeleted,
    "Prompt apply config deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      config.deleted_at_set === true,
      "Prompt apply config delete did not set deleted_at.",
    );
  } else {
    assert(
      config.status === "Completed",
      "Prompt apply config did not use the default completed status.",
    );
    assert(
      config.error_localizer === false,
      "Prompt apply config did not persist error_localizer=false.",
    );
  }
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      config.mapping?.[key] === value,
      `Prompt apply config did not preserve mapping for ${key}.`,
    );
  }
}

function assertEvalGroupSimulateApplyDbAudit(
  audit,
  {
    groupId,
    runTestId,
    organizationId,
    workspaceId,
    templateId,
    configId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit.group_id === groupId,
    "Simulate apply DB audit group id mismatch.",
  );
  assert(
    audit.run_test_id === runTestId,
    "Simulate apply DB audit run-test id mismatch.",
  );
  assert(
    audit.run_test_organization_id === organizationId,
    "Simulate apply DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.run_test_workspace_id === workspaceId,
    "Simulate apply DB audit workspace did not match request context.",
  );
  assert(
    Number(audit.config_count) === 1,
    "Simulate apply should create exactly one config for the temporary group.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Simulate apply DB audit active config count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Simulate apply DB audit deleted config count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Simulate apply cleanup left a deleted config without deleted_at.",
  );
  assert(
    Number(audit.run_test_active_config_count) >= (expectedDeleted ? 1 : 2),
    "Simulate apply cleanup violated the run-test active eval config invariant.",
  );

  const configs = payloadArray(audit.configs, "configs");
  const config = configs[0];
  assert(
    config?.id === configId,
    "Simulate apply DB audit config id mismatch.",
  );
  assert(
    config.eval_template_id === templateId,
    "Simulate apply DB audit used wrong template.",
  );
  assert(
    config.deleted === expectedDeleted,
    "Simulate apply config deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      config.deleted_at_set === true,
      "Simulate apply config delete did not set deleted_at.",
    );
  } else {
    assert(
      config.status === "Completed",
      "Simulate apply config did not use the default completed status.",
    );
    assert(
      config.model === "turing_small",
      "Simulate apply config did not persist selected model.",
    );
    assert(
      config.error_localizer === false,
      "Simulate apply config did not persist error_localizer=false.",
    );
  }
  assert(
    Array.isArray(config.filters) && config.filters.length === 0,
    "Simulate apply config did not persist canonical empty filters list.",
  );
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      config.mapping?.[key] === value,
      `Simulate apply config did not preserve mapping for ${key}.`,
    );
    assert(
      config.config?.mapping?.[key] === value,
      `Simulate apply nested config did not preserve mapping for ${key}.`,
    );
  }
  assert(
    config.config?.reason_column === true,
    "Simulate apply config did not enable reason_column.",
  );
}

function assertEvalGroupExperimentApplyDbAudit(
  audit,
  {
    groupId,
    experimentId,
    datasetId,
    organizationId,
    workspaceId,
    templateId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit.group_id === groupId,
    "Experiment apply DB audit group id mismatch.",
  );
  assert(
    audit.experiment_id === experimentId,
    "Experiment apply DB audit experiment id mismatch.",
  );
  assert(
    audit.dataset_id === datasetId,
    "Experiment apply DB audit dataset id mismatch.",
  );
  assert(
    audit.dataset_organization_id === organizationId,
    "Experiment apply DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.dataset_workspace_id === workspaceId,
    "Experiment apply DB audit workspace did not match request context.",
  );
  assert(
    Number(audit.metric_count) === 1,
    "Experiment apply should create exactly one metric for the temporary group.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Experiment apply DB audit active metric count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Experiment apply DB audit deleted metric count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Experiment apply cleanup left a deleted metric without deleted_at.",
  );

  const metrics = payloadArray(audit.metrics, "metrics");
  const metric = metrics[0];
  assert(
    metric?.template_id === templateId,
    "Experiment apply used wrong template.",
  );
  assert(
    metric.organization_id === organizationId,
    "Experiment apply metric organization did not match request context.",
  );
  assert(
    !workspaceId || metric.workspace_id === workspaceId,
    "Experiment apply metric workspace did not match request context.",
  );
  assert(
    metric.source_id === experimentId,
    "Experiment apply metric source_id did not point at the experiment.",
  );
  assert(
    Number(metric.m2m_link_count) === 1,
    "Experiment apply metric was not linked to the experiment M2M.",
  );
  assert(
    metric.deleted === expectedDeleted,
    "Experiment apply metric deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      metric.deleted_at_set === true,
      "Experiment apply metric delete did not set deleted_at.",
    );
  } else {
    assert(
      metric.status === "ExperimentEvaluation",
      "Experiment apply metric did not use ExperimentEvaluation status.",
    );
    assert(
      metric.model === "turing_small",
      "Experiment apply metric did not persist selected model.",
    );
    assert(
      metric.error_localizer === false,
      "Experiment apply metric did not persist error_localizer=false.",
    );
  }

  const configMapping = metric.config?.mapping || {};
  for (const [key, columnId] of Object.entries(mapping)) {
    assert(
      configMapping[key] === columnId,
      `Experiment apply metric config did not preserve mapping for ${key}.`,
    );
  }
  assert(
    metric.config?.reason_column === true,
    "Experiment apply metric config did not enable reason_column.",
  );
}

function assertEvalGroupEvalTaskApplyDbAudit(
  audit,
  {
    groupId,
    projectId,
    organizationId,
    workspaceId,
    templateId,
    configId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit.group_id === groupId,
    "Eval-task apply DB audit group id mismatch.",
  );
  assert(
    audit.project_id === projectId,
    "Eval-task apply DB audit project id mismatch.",
  );
  assert(
    audit.project_organization_id === organizationId,
    "Eval-task apply DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.project_workspace_id === workspaceId,
    "Eval-task apply DB audit workspace did not match request context.",
  );
  assert(
    Number(audit.config_count) === 1,
    "Eval-task apply should create exactly one custom config for the temporary group.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Eval-task apply DB audit active config count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Eval-task apply DB audit deleted config count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Eval-task apply cleanup left a deleted custom config without deleted_at.",
  );

  const configs = payloadArray(audit.configs, "configs");
  const config = configs[0];
  assert(
    config?.id === configId,
    "Eval-task apply DB audit config id mismatch.",
  );
  assert(
    config.eval_template_id === templateId,
    "Eval-task apply DB audit used wrong template.",
  );
  assert(
    config.eval_group_id === groupId,
    "Eval-task apply DB audit config was not linked to the eval group.",
  );
  assert(
    config.project_id === projectId,
    "Eval-task apply DB audit config was not linked to the project.",
  );
  assert(
    config.deleted === expectedDeleted,
    "Eval-task apply config deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      config.deleted_at_set === true,
      "Eval-task apply config delete did not set deleted_at.",
    );
  } else {
    assert(
      config.model === "turing_small",
      "Eval-task apply config did not persist selected model.",
    );
    assert(
      config.error_localizer === false,
      "Eval-task apply config did not persist error_localizer=false.",
    );
  }
  for (const [key, value] of Object.entries(mapping)) {
    assert(
      config.mapping?.[key] === value,
      `Eval-task apply config did not preserve mapping for ${key}.`,
    );
  }
}

function assertEvalTemplateDetail(detail, templateId, name) {
  assert(detail?.id === templateId, "Eval template detail returned wrong id.");
  assert(detail.name === name, "Eval template detail returned wrong name.");
  assert(
    detail.owner === "user",
    "Eval template detail did not mark owner=user.",
  );
  assert(
    detail.eval_type === "code",
    "Eval template detail did not preserve eval_type=code.",
  );
  assert(
    detail.output_type === "pass_fail",
    "Eval template detail did not preserve output_type=pass_fail.",
  );
  assert(
    detail.current_version === "V1",
    "New eval template detail did not start on V1.",
  );
}

async function createCompositeChildEval(client, cleanup, name, codeBody) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        `    ${codeBody}`,
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Composite child created by API journey regression.",
      tags: ["api-journey", "composite-child"],
    },
  );
  assert(
    isUuid(created?.id),
    "Composite child eval create did not return a UUID id.",
  );
  cleanup.defer(`delete API journey composite child ${name}`, () =>
    client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
      template_ids: [created.id],
    }),
  );
  return created;
}

async function createCompositeRequiredKeyChildEval(
  client,
  cleanup,
  name,
  instructions,
) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "llm",
      instructions,
      model: "turing_large",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description:
        "Composite child with required keys for API journey regression.",
      tags: ["api-journey", "composite-child"],
      check_internet: false,
    },
  );
  assert(
    isUuid(created?.id),
    "Composite required-key child eval create did not return a UUID id.",
  );
  cleanup.defer(`delete API journey composite child ${name}`, () =>
    client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
      template_ids: [created.id],
    }),
  );
  return created;
}

async function firstEvalTemplateVersion(client, templateId) {
  const versions = await client.get(
    apiPath("/model-hub/eval-templates/{template_id}/versions/", {
      template_id: templateId,
    }),
  );
  const items = asArray(versions?.versions || versions);
  const first = items[0];
  assert(
    isUuid(first?.id),
    "Eval template versions endpoint did not return a UUID version id.",
  );
  return first;
}

async function createEvalPlaygroundCodeEval(client, name) {
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "code",
      code: [
        "def evaluate(output=None, expected=None, **kwargs):",
        "    return str(output).strip().lower() == str(expected).strip().lower()",
      ].join("\n"),
      code_language: "python",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description:
        "Eval playground template created by API journey regression.",
      tags: ["api-journey", "eval-playground"],
    },
  );
  assert(
    isUuid(created?.id),
    "Eval playground eval create did not return a UUID id.",
  );
  return created;
}

function testEvaluationPayload({
  name,
  templateId,
  evalTypeId,
  mapping,
  params,
}) {
  return {
    name,
    template_type: "Function",
    template_id: templateId,
    eval_type_id: evalTypeId,
    model: "",
    output_type: "Pass/Fail",
    required_keys: Object.keys(mapping || {}),
    input_data_types: Object.fromEntries(
      Object.keys(mapping || {}).map((key) => [key, "text"]),
    ),
    config: {
      mapping,
      config: {},
      params,
    },
  };
}

function assertTestEvaluationPayload(payload, expectedOutput) {
  assert(
    payload && typeof payload === "object",
    "test-evaluation returned no payload.",
  );
  assert(
    isUuid(payload.log_id),
    "test-evaluation response did not include a usage log id.",
  );
  const actual = normalizeEvalOutput(payload.output);
  assert(
    actual === expectedOutput.toLowerCase(),
    `test-evaluation returned output ${payload.output}, expected ${expectedOutput}.`,
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "reason"),
    "test-evaluation response did not include reason.",
  );
  assert(
    payload.output_type === "Pass/Fail" || payload.output_type === "pass_fail",
    "test-evaluation response did not preserve Pass/Fail output type.",
  );
}

function normalizeEvalOutput(value) {
  if (value === true) return "passed";
  if (value === false) return "failed";
  return String(value || "")
    .trim()
    .toLowerCase();
}

function assertEvalPlaygroundExecutionPayload(payload) {
  assert(
    payload && typeof payload === "object",
    "Eval playground returned no payload.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "output"),
    "Eval playground response did not include output.",
  );
  assert(
    isUuid(payload.log_id),
    "Eval playground response did not include log_id.",
  );
}

function assertEvalPlaygroundLogDetail(payload, logId) {
  assert(
    payload && typeof payload === "object",
    "Eval log detail returned no payload.",
  );
  assert(
    String(payload.log_id) === logId || String(payload.evaluation_id) === logId,
    "Eval log detail returned the wrong log id.",
  );
  assert(
    payload.source === "Eval Playground",
    "Eval log detail did not identify the eval playground source.",
  );
  assert(
    payload.output && typeof payload.output === "object",
    "Eval log detail did not include output details.",
  );
}

function assertEvalPlaygroundErrorLocalizerDetail(payload, logId) {
  assert(
    String(payload?.log_id) === logId ||
      String(payload?.evaluation_id) === logId,
    "Error-localizer log detail returned the wrong log id.",
  );
  const status = String(payload?.error_localizer_status || "");
  assert(
    ["pending", "running", "completed", "failed", "skipped"].includes(status),
    "Eval log detail did not expose an error-localizer task status.",
  );
  if (status === "completed") {
    assert(
      payload.error_details && typeof payload.error_details === "object",
      "Completed error-localizer task did not expose error_details.",
    );
  }
}

function assertEvalPlaygroundConfigPayload(
  payload,
  { templateId, templateName },
) {
  const config = payload?.eval || payload;
  assert(
    config && typeof config === "object",
    "Eval config returned no payload.",
  );
  assert(
    config.id === templateId || config.template_id === templateId,
    "Eval config returned the wrong template id.",
  );
  assert(
    config.name === templateName || config.template_name === templateName,
    "Eval config returned the wrong template name.",
  );
  assert(
    config.owner === "user" || payload?.owner === "user",
    "Eval config did not mark the disposable template as user-owned.",
  );
  assert(
    config.output === "Pass/Fail" || config.output === "pass_fail",
    "Eval config did not preserve the pass/fail output type.",
  );
}

function assertEvalPlaygroundTemplateNamePayload(
  payload,
  { templateId, templateName, expectedPresent },
) {
  const rows = payloadArray(payload, "templates");
  const match = rows.find(
    (row) => row?.id === templateId || row?.name === templateName,
  );
  assert(
    Boolean(match) === expectedPresent,
    expectedPresent
      ? "Eval template name picker did not include the disposable template."
      : "Eval template name picker still included the deleted disposable template.",
  );
}

function assertEvalPlaygroundUsageTemplateList(
  payload,
  { templateId, templateName, expectedPresent },
) {
  const rows = payloadArray(payload?.row_data, "row_data");
  const match = rows.find(
    (row) => row?.id === templateId || row?.eval_template_name === templateName,
  );
  assert(
    Boolean(match) === expectedPresent,
    expectedPresent
      ? "Eval usage template list did not include the disposable template."
      : "Eval usage template list still included the deleted disposable template.",
  );
}

function assertEvalPlaygroundLogTable(
  payload,
  { logId, expectedCellText, expectedSearchHighlight = false },
) {
  assert(
    payload && typeof payload === "object",
    "Eval log table endpoint returned no payload.",
  );
  const rows = payloadArray(payload.table, "table");
  const row = rows.find((item) => String(item?.log_id) === logId);
  assert(row, "Eval log table did not include the playground log.");
  assert(
    Number(payload?.metadata?.total_rows ?? rows.length) >= 1,
    "Eval log table metadata did not count the playground log.",
  );
  const columns = payloadArray(payload.column_config, "column_config");
  const outputColumn = columns.find((column) => column?.name === "output");
  assert(outputColumn?.id, "Eval log table omitted the output column config.");
  const outputCell = row[outputColumn.id];
  assert(
    String(outputCell?.cell_value || "").includes(expectedCellText),
    "Eval log table row did not include the playground input value.",
  );
  if (expectedSearchHighlight) {
    assert(
      outputCell?.key_exists === true,
      "Eval log table search did not mark the matching cell.",
    );
  }
  return outputColumn;
}

function assertEvalPlaygroundFeedbackList(
  payload,
  feedbackId,
  explanation,
  { expectedActionType } = {},
) {
  const rows = payloadArray(payload?.items, "items");
  const row = rows.find((item) => item?.id === feedbackId);
  assert(row, "Eval feedback list did not include playground feedback.");
  assert(
    row.explanation === explanation,
    "Eval feedback list did not include the updated feedback explanation.",
  );
  assert(
    row.source === "eval_playground",
    "Eval feedback list did not preserve source=eval_playground.",
  );
  if (expectedActionType) {
    assert(
      row.action_type === expectedActionType,
      "Eval feedback list did not include the expected action_type.",
    );
  }
}

function assertTestEvaluationDbAudit(
  audit,
  {
    templateId,
    evalTypeId,
    logIds,
    organizationId,
    workspaceId,
    expectedLogsDeleted,
  },
) {
  assert(
    audit?.template_id === templateId,
    "test-evaluation DB audit returned wrong template.",
  );
  assert(
    !audit.template_organization_id ||
      audit.template_organization_id === organizationId,
    "test-evaluation template organization was neither system-owned nor request-owned.",
  );
  if (workspaceId) {
    assert(
      !audit.template_workspace_id ||
        audit.template_workspace_id === workspaceId,
      "test-evaluation template workspace was neither system-owned nor request-owned.",
    );
  }
  assert(
    audit.template_deleted === false,
    "test-evaluation system template was unexpectedly deleted.",
  );
  assert(
    audit.template_eval_type_id === evalTypeId,
    "test-evaluation template DB audit did not preserve the selected eval_type_id.",
  );

  const logs = asArray(audit.logs);
  assert(
    logs.length === logIds.length,
    "test-evaluation DB audit returned the wrong number of usage logs.",
  );
  const logsById = new Map(logs.map((row) => [row.log_id, row]));
  const outputs = new Set();
  for (const logId of logIds) {
    const row = logsById.get(logId);
    assert(row, `test-evaluation DB audit missing log ${logId}.`);
    assert(
      row.organization_id === organizationId,
      "test-evaluation log organization did not match request context.",
    );
    if (workspaceId) {
      assert(
        row.workspace_id === workspaceId,
        "test-evaluation log workspace did not match request context.",
      );
    }
    assert(
      row.source === "eval_playground_test",
      "test-evaluation log did not use source=eval_playground_test.",
    );
    assert(
      row.source_id === templateId,
      "test-evaluation log source_id did not point at the eval template.",
    );
    assert(
      String(row.status || "").toLowerCase() === "success",
      "test-evaluation log did not finish successfully.",
    );
    assert(
      row.deleted === expectedLogsDeleted,
      "test-evaluation log deleted state mismatch.",
    );
    if (expectedLogsDeleted) {
      assert(
        row.deleted_at_set === true,
        "test-evaluation log delete did not stamp deleted_at.",
      );
    }
    const requiredKeys = asArray(row.required_keys);
    assert(
      requiredKeys.includes("text"),
      "test-evaluation log did not persist text as a required key.",
    );
    const mappings = row.mappings || {};
    assert(
      Object.prototype.hasOwnProperty.call(mappings, "text"),
      "test-evaluation log did not persist text mapping.",
    );
    outputs.add(normalizeEvalOutput(row.output));
  }
  assert(
    outputs.has("passed") && outputs.has("failed"),
    "test-evaluation DB audit did not include both pass and fail outputs.",
  );
}

function assertEvalPlaygroundDbAudit(
  audit,
  {
    templateId,
    logId,
    feedbackId,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedFeedbackExplanation,
    expectedFeedbackActionType,
  },
) {
  assert(
    audit?.template_id === templateId,
    "Playground DB audit returned wrong template.",
  );
  assert(audit.log_id === logId, "Playground DB audit returned wrong log.");
  assert(
    audit.template_organization_id === organizationId,
    "Playground template DB audit organization did not match request context.",
  );
  assert(
    audit.log_organization_id === organizationId,
    "Playground log DB audit organization did not match request context.",
  );
  if (workspaceId) {
    assert(
      audit.template_workspace_id === workspaceId,
      "Playground template DB audit workspace did not match request context.",
    );
    assert(
      audit.log_workspace_id === workspaceId,
      "Playground log DB audit workspace did not match request context.",
    );
  }
  assert(
    audit.log_source === "eval_playground",
    "Playground log DB audit did not preserve source=eval_playground.",
  );
  assert(
    audit.log_source_id === templateId,
    "Playground log DB audit source_id did not point at the eval template.",
  );

  if (expectedDeleted) {
    assert(
      audit.template_deleted === true,
      "Playground template was not deleted.",
    );
    assert(
      audit.template_deleted_at_set === true,
      "Playground template delete did not stamp deleted_at.",
    );
    assert(audit.log_deleted === true, "Playground log was not deleted.");
    assert(
      audit.log_deleted_at_set === true,
      "Playground log delete did not stamp deleted_at.",
    );
    assert(
      audit.feedback_absent_or_deleted === true,
      "Playground feedback cleanup left an active feedback row.",
    );
    return;
  }

  assert(
    audit.template_deleted === false,
    "Playground template was deleted early.",
  );
  assert(audit.log_deleted === false, "Playground log was deleted early.");
  assert(
    audit.feedback_id === feedbackId,
    "Playground DB audit returned wrong feedback row.",
  );
  assert(
    audit.feedback_organization_id === organizationId,
    "Playground feedback DB audit organization did not match request context.",
  );
  assert(
    audit.feedback_source === "eval_playground",
    "Playground feedback DB audit did not preserve source=eval_playground.",
  );
  assert(
    audit.feedback_source_id === logId,
    "Playground feedback DB audit source_id did not point at the log id.",
  );
  assert(
    audit.feedback_eval_template_id === templateId,
    "Playground feedback DB audit did not point at the eval template.",
  );
  assert(
    audit.feedback_value === "failed",
    "Playground feedback update did not persist the latest value.",
  );
  assert(
    audit.feedback_explanation === expectedFeedbackExplanation,
    "Playground feedback update did not persist the latest explanation.",
  );
  if (expectedFeedbackActionType) {
    assert(
      audit.feedback_action_type === expectedFeedbackActionType,
      "Playground feedback update did not persist the latest action_type.",
    );
  }
}

function assertEvalPlaygroundErrorLocalizerAudit(
  audit,
  { templateId, logId, organizationId, workspaceId, expectedDeleted },
) {
  assert(audit?.task_exists === true, "Error-localizer audit found no task.");
  assert(
    audit.task_source === "playground",
    "Error-localizer task did not preserve source=playground.",
  );
  assert(
    audit.task_source_id === logId,
    "Error-localizer task source_id did not point at the playground log.",
  );
  assert(
    audit.task_eval_template_id === templateId,
    "Error-localizer task did not point at the eval template.",
  );
  assert(
    audit.task_organization_id === organizationId,
    "Error-localizer task organization did not match request context.",
  );
  if (workspaceId) {
    assert(
      audit.task_workspace_id === workspaceId,
      "Error-localizer task workspace did not match request context.",
    );
  }
  assert(
    ["pending", "running", "completed", "failed", "skipped"].includes(
      String(audit.task_status || ""),
    ),
    "Error-localizer task stored an unexpected status.",
  );
  assert(
    audit.task_input_data?.output === "ungrounded answer",
    "Error-localizer task did not preserve output input_data.",
  );
  assert(
    audit.task_input_data?.expected === "grounded answer",
    "Error-localizer task did not preserve expected input_data.",
  );
  assert(
    audit.task_deleted === expectedDeleted,
    "Error-localizer task deleted flag did not match expected cleanup state.",
  );
  if (expectedDeleted) {
    assert(
      audit.task_deleted_at_set === true,
      "Error-localizer task cleanup did not stamp deleted_at.",
    );
  }
}

function assertEvalPlaygroundSettingsAudit(
  audit,
  { expectedDeleted, expectedFirstColumnVisible },
) {
  assert(
    audit?.setting_exists === true,
    "Eval log settings audit found no row.",
  );
  assert(
    Number(audit.setting_count) === 1,
    "Eval log settings audit found duplicate settings rows.",
  );
  assert(
    audit.setting_deleted === expectedDeleted,
    "Eval log settings deleted flag did not match expected cleanup state.",
  );
  if (expectedDeleted) {
    assert(
      audit.setting_deleted_at_set === true,
      "Eval log settings cleanup did not stamp deleted_at.",
    );
  }
  assert(
    Number(audit.column_count) >= 1,
    "Eval log settings audit did not persist column config.",
  );
  assert(
    audit.first_column_id === "column1",
    "Eval log settings audit returned the wrong first column.",
  );
  assert(
    audit.first_column_visible === expectedFirstColumnVisible,
    "Eval log settings audit did not persist first-column visibility.",
  );
}

function assertCompositeDetail(
  payload,
  {
    compositeId,
    name,
    childIds,
    aggregationFunction,
    weights,
    pinnedVersionIds = {},
  },
) {
  assert(payload?.id === compositeId, "Composite detail returned wrong id.");
  assert(payload.name === name, "Composite detail returned wrong name.");
  assert(
    payload.template_type === "composite",
    "Composite detail did not expose template_type=composite.",
  );
  assert(
    payload.aggregation_enabled === true,
    "Composite detail did not persist aggregation_enabled=true.",
  );
  assert(
    payload.aggregation_function === aggregationFunction,
    "Composite detail returned wrong aggregation_function.",
  );
  assert(
    payload.composite_child_axis === "pass_fail",
    "Composite detail returned wrong composite_child_axis.",
  );
  const children = payloadArray(payload.children, "children");
  assert(
    children.length === childIds.length,
    "Composite detail returned the wrong number of children.",
  );
  const byId = new Map(children.map((child) => [child.child_id, child]));
  for (const [index, childId] of childIds.entries()) {
    const child = byId.get(childId);
    assert(child, `Composite detail omitted child ${childId}.`);
    assert(
      Number(child.order) === index,
      `Composite child ${childId} did not preserve request order.`,
    );
    assert(
      Number(child.weight) === Number(weights[childId]),
      `Composite child ${childId} did not preserve weight.`,
    );
    if (pinnedVersionIds[childId]) {
      assert(
        child.pinned_version_id === pinnedVersionIds[childId],
        `Composite child ${childId} did not preserve pinned version.`,
      );
    }
  }
}

function assertCompositeExecutePayload(
  payload,
  { compositeId, name, childIds },
) {
  assert(
    payload?.composite_id === compositeId,
    "Composite execute returned wrong composite_id.",
  );
  assert(
    payload.composite_name === name,
    "Composite execute returned wrong composite_name.",
  );
  assert(
    Number(payload.total_children) === childIds.length,
    "Composite execute returned wrong total_children.",
  );
  assert(
    Number(payload.completed_children) === childIds.length,
    "Composite execute did not complete every child.",
  );
  assert(
    Number(payload.failed_children) === 0,
    "Composite execute reported failed children.",
  );
  const children = payloadArray(payload.children, "children");
  assert(
    children.length === childIds.length,
    "Composite execute returned the wrong number of child results.",
  );
  const resultIds = children.map((child) => child.child_id).sort();
  assert(
    resultIds.join(",") === [...childIds].sort().join(","),
    "Composite execute child result ids did not match composite children.",
  );
  assert(
    typeof payload.aggregate_pass === "boolean",
    "Composite execute did not return aggregate_pass.",
  );
}

function assertEvalUsagePayload(payload, templateId) {
  assert(
    payload?.template_id === templateId,
    "Eval usage endpoint returned wrong template id.",
  );
  assert(
    payload.stats && typeof payload.stats === "object",
    "Eval usage endpoint did not return stats.",
  );
  assert(
    Array.isArray(payload.chart),
    "Eval usage endpoint did not return chart array.",
  );
  assert(
    Array.isArray(payload.logs?.items),
    "Eval usage endpoint did not return log items array.",
  );
}

function assertEvalFeedbackListPayload(payload, templateId) {
  assert(
    payload?.template_id === templateId,
    "Eval feedback-list endpoint returned wrong template id.",
  );
  assert(
    Array.isArray(payload.items),
    "Eval feedback-list endpoint did not return items array.",
  );
  assert(
    Number.isInteger(Number(payload.total)),
    "Eval feedback-list endpoint did not return numeric total.",
  );
}

function assertEvalChartsPayload(payload, templateId) {
  const chart = payload?.charts?.[templateId];
  assert(chart, "Eval list-charts endpoint omitted the selected template.");
  assert(
    Array.isArray(chart.chart),
    "Eval list-charts chart was not an array.",
  );
  assert(
    Array.isArray(chart.error_rate),
    "Eval list-charts error_rate was not an array.",
  );
  assert(
    Number.isInteger(Number(chart.run_count)),
    "Eval list-charts run_count was not numeric.",
  );
}

function assertEvalMetricPayload(payload, templateId) {
  assert(
    payload?.base_eval_template_id === templateId,
    "get-eval-metrics returned wrong base eval template id.",
  );
  assert(
    payload.api_call_count && typeof payload.api_call_count === "object",
    "get-eval-metrics did not return api_call_count.",
  );
  assert(
    payload.average && typeof payload.average === "object",
    "get-eval-metrics did not return average.",
  );
}

function assertEvalVersionsPayload(payload, templateId, minimumTotal) {
  assert(
    payload?.template_id === templateId,
    "Eval versions endpoint returned wrong template id.",
  );
  assert(
    Array.isArray(payload.versions),
    "Eval versions endpoint did not return versions array.",
  );
  assert(
    Number(payload.total) >= minimumTotal,
    `Eval versions endpoint returned fewer than ${minimumTotal} versions.`,
  );
}

function assertGroundTruthListPayload(payload, templateId, groundTruthId) {
  assert(
    payload?.template_id === templateId,
    "Ground-truth list returned wrong template id.",
  );
  assert(
    payload.items?.some((item) => item.id === groundTruthId),
    "Ground-truth list did not include uploaded ground truth.",
  );
  assert(
    Number(payload.total) >= 1,
    "Ground-truth list total did not include uploaded row.",
  );
}

function assertGroundTruthDataPayload(payload, groundTruthId) {
  assert(payload?.id === groundTruthId, "Ground-truth data returned wrong id.");
  assert(
    payload.total_rows === 2,
    "Ground-truth data did not return the expected row count.",
  );
  assert(
    Array.isArray(payload.rows) && payload.rows.length === 2,
    "Ground-truth data did not return uploaded rows.",
  );
  assert(
    payload.columns?.includes("question") &&
      payload.columns?.includes("answer"),
    "Ground-truth data did not return uploaded columns.",
  );
}

function assertGroundTruthStatusPayload(payload, groundTruthId) {
  assert(
    payload?.id === groundTruthId,
    "Ground-truth status returned wrong id.",
  );
  assert(
    payload.embedding_status === "pending",
    "New ground truth should remain pending until embed is triggered.",
  );
  assert(
    payload.total_rows === 2,
    "Ground-truth status did not return the expected row count.",
  );
}

function assertEvalTemplateLifecycleDbAudit(
  audit,
  {
    templateId,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedVersions,
    groundTruthId,
  },
) {
  assert(audit.template_id === templateId, "Eval DB audit returned wrong id.");
  assert(
    audit.organization_id === organizationId,
    "Eval DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Eval DB audit workspace did not match request context.",
  );
  assert(audit.owner === "user", "Eval DB audit did not persist owner=user.");
  assert(
    audit.deleted === expectedDeleted,
    "Eval DB audit deleted state did not match expected state.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Eval template soft-delete did not set deleted_at.",
    );
  }
  assert(
    Number(audit.version_count) >= expectedVersions,
    "Eval DB audit version count was lower than expected.",
  );
  assert(
    Number(audit.default_version_count) === 1,
    "Eval DB audit expected exactly one default version.",
  );
  assert(
    audit.ground_truth_id === groundTruthId,
    "Eval DB audit ground-truth config did not reference uploaded ground truth.",
  );
}

function assertCustomEvalCreateDbAudit(
  audit,
  { templateId, templateName, organizationId, workspaceId, expectedDeleted },
) {
  assert(
    audit?.template_id === templateId,
    "Custom eval audit returned wrong id.",
  );
  assert(
    audit.name === templateName,
    "Custom eval audit returned wrong template name.",
  );
  assert(
    audit.organization_id === organizationId,
    "Custom eval audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Custom eval audit workspace did not match request context.",
  );
  assert(
    audit.owner === "user",
    "Custom eval audit did not persist owner=user.",
  );
  assert(
    audit.deleted === expectedDeleted,
    "Custom eval audit deleted state did not match expected state.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Custom eval soft-delete did not set deleted_at.",
    );
  }
  assert(
    audit.eval_type_id === "DeterministicEvaluator",
    "Custom eval config did not persist DeterministicEvaluator.",
  );
  assert(
    audit.config_output === "Pass/Fail",
    "Custom eval config did not persist Pass/Fail output.",
  );
  assert(
    asArray(audit.required_keys).includes("response"),
    "Custom eval config did not persist required_keys=response.",
  );
  assert(
    audit.custom_eval === true,
    "Custom eval config did not persist custom_eval=true.",
  );
  assert(
    audit.check_internet === false,
    "Custom eval config did not persist check_internet=false.",
  );
  assert(
    audit.criteria === "Judge {{response}} for clarity.",
    "Custom eval audit did not persist criteria.",
  );
  assert(
    asArray(audit.eval_tags).includes("api-journey"),
    "Custom eval audit did not persist tags.",
  );
  assert(
    Number(audit.version_count) >= 1,
    "Custom eval create did not create an initial version.",
  );
  assert(
    Number(audit.default_version_count) === 1,
    "Custom eval create did not create exactly one default version.",
  );
  assert(
    Number(audit.version_workspace_mismatch_count) === 0,
    "Custom eval version workspace did not match template workspace.",
  );
}

function assertCompositeEvalDbAudit(
  audit,
  {
    compositeId,
    childIds,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedActiveChildren,
    expectedVersions,
  },
) {
  assert(
    audit.composite_id === compositeId,
    "Composite DB audit returned wrong composite id.",
  );
  assert(
    audit.organization_id === organizationId,
    "Composite DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Composite DB audit workspace did not match request context.",
  );
  assert(
    audit.template_type === "composite",
    "Composite DB audit did not persist template_type=composite.",
  );
  assert(
    audit.owner === "user",
    "Composite DB audit did not persist owner=user.",
  );
  assert(
    audit.deleted === expectedDeleted,
    "Composite DB audit deleted state did not match expected state.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Composite soft-delete did not set deleted_at.",
    );
  }
  assert(
    Number(audit.active_child_count) === expectedActiveChildren,
    "Composite DB audit active child count mismatch.",
  );
  assert(
    Number(audit.version_count) >= expectedVersions,
    "Composite DB audit version count was lower than expected.",
  );
  const auditedChildIds = payloadArray(audit.child_ids, "child_ids").sort();
  assert(
    auditedChildIds.join(",") === [...childIds].sort().join(","),
    "Composite DB audit child ids did not match request children.",
  );
}

function assertCompositeDatasetBindingDbAudit(
  audit,
  {
    datasetId,
    templateId,
    metricName,
    organizationId,
    workspaceId,
    mapping,
    weightOverrides,
    expectedDeleted,
  },
) {
  assert(
    audit.dataset_id === datasetId,
    "Composite dataset binding DB audit returned wrong dataset id.",
  );
  assert(
    audit.template_id === templateId,
    "Composite dataset binding DB audit returned wrong template id.",
  );
  assert(
    audit.metric_name === metricName,
    "Composite dataset binding DB audit returned wrong metric name.",
  );
  assert(
    Number(audit.metric_count) === 1,
    "Composite dataset binding should create exactly one audited metric.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Composite dataset binding active metric count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Composite dataset binding deleted metric count mismatch.",
  );
  const metric = payloadArray(audit.metrics, "metrics")[0];
  assert(
    metric?.template_id === templateId,
    "Composite binding used wrong template.",
  );
  assert(
    metric.template_type === "composite",
    "Composite binding metric did not reference a composite template.",
  );
  assert(
    metric.organization_id === organizationId,
    "Composite binding metric organization did not match request context.",
  );
  assert(
    !workspaceId || metric.workspace_id === workspaceId,
    "Composite binding metric workspace did not match request context.",
  );
  assert(
    metric.deleted === expectedDeleted,
    "Composite binding metric deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      metric.deleted_at_set === true,
      "Composite binding metric delete did not set deleted_at.",
    );
  } else {
    assert(
      metric.status === "Inactive",
      "Composite binding metric was not inactive.",
    );
    assert(
      metric.model === "turing_small",
      "Composite binding metric did not persist selected model.",
    );
    assert(
      metric.error_localizer === false,
      "Composite binding metric did not persist error_localizer=false.",
    );
  }
  for (const [key, columnId] of Object.entries(mapping)) {
    assert(
      metric.config?.mapping?.[key] === columnId,
      `Composite binding config did not preserve mapping for ${key}.`,
    );
  }
  for (const [childId, weight] of Object.entries(weightOverrides)) {
    assert(
      Number(metric.composite_weight_overrides?.[childId]) === Number(weight),
      `Composite binding did not persist weight override for ${childId}.`,
    );
  }
  assert(
    metric.config?.reason_column === true,
    "Composite binding metric config did not default reason_column.",
  );
}

function assertCompositeExperimentBindingDbAudit(
  audit,
  {
    experimentId,
    templateId,
    metricName,
    organizationId,
    workspaceId,
    mapping,
    weightOverrides,
    expectedDeleted,
  },
) {
  assert(
    audit.experiment_id === experimentId,
    "Composite experiment binding DB audit returned wrong experiment id.",
  );
  assert(
    audit.template_id === templateId,
    "Composite experiment binding DB audit returned wrong template id.",
  );
  assert(
    audit.metric_name === metricName,
    "Composite experiment binding DB audit returned wrong metric name.",
  );
  assert(
    audit.dataset_organization_id === organizationId,
    "Composite experiment binding dataset organization did not match request context.",
  );
  assert(
    !workspaceId || audit.dataset_workspace_id === workspaceId,
    "Composite experiment binding dataset workspace did not match request context.",
  );
  assert(
    Number(audit.metric_count) === 1,
    "Composite experiment binding should create exactly one audited metric.",
  );
  assert(
    Number(audit.active_count) === (expectedDeleted ? 0 : 1),
    "Composite experiment binding active metric count mismatch.",
  );
  assert(
    Number(audit.deleted_count) === (expectedDeleted ? 1 : 0),
    "Composite experiment binding deleted metric count mismatch.",
  );
  assert(
    Number(audit.deleted_without_deleted_at_count) === 0,
    "Composite experiment binding cleanup left a deleted metric without deleted_at.",
  );

  const metric = payloadArray(audit.metrics, "metrics")[0];
  assert(
    metric?.template_id === templateId,
    "Composite experiment binding used wrong template.",
  );
  assert(
    metric.template_type === "composite",
    "Composite experiment binding metric did not reference a composite template.",
  );
  assert(
    metric.organization_id === organizationId,
    "Composite experiment binding metric organization did not match request context.",
  );
  assert(
    !workspaceId || metric.workspace_id === workspaceId,
    "Composite experiment binding metric workspace did not match request context.",
  );
  assert(
    metric.source_id === experimentId,
    "Composite experiment binding metric source_id did not point at the experiment.",
  );
  assert(
    metric.dataset_id === audit.eval_dataset_id,
    "Composite experiment binding metric dataset did not match experiment eval dataset.",
  );
  assert(
    Number(metric.m2m_link_count) === 1,
    "Composite experiment binding metric was not linked to the experiment M2M.",
  );
  assert(
    metric.deleted === expectedDeleted,
    "Composite experiment binding metric deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      metric.deleted_at_set === true,
      "Composite experiment binding metric delete did not set deleted_at.",
    );
  } else {
    assert(
      metric.status === "ExperimentEvaluation",
      "Composite experiment binding metric did not use ExperimentEvaluation status.",
    );
    assert(
      metric.model === "turing_small",
      "Composite experiment binding metric did not persist selected model.",
    );
    assert(
      metric.error_localizer === false,
      "Composite experiment binding metric did not persist error_localizer=false.",
    );
  }

  const configMapping = metric.config?.mapping || {};
  for (const [key, columnId] of Object.entries(mapping)) {
    assert(
      configMapping[key] === columnId,
      `Composite experiment binding config did not preserve mapping for ${key}.`,
    );
  }
  for (const [childId, weight] of Object.entries(weightOverrides)) {
    assert(
      Number(metric.composite_weight_overrides?.[childId]) === Number(weight),
      `Composite experiment binding did not persist weight override for ${childId}.`,
    );
  }
  assert(
    metric.config?.reason_column === true,
    "Composite experiment binding metric config did not default reason_column.",
  );
}

function assertGroundTruthDbAudit(
  audit,
  { groundTruthId, templateId, organizationId, workspaceId, expectedDeleted },
) {
  assert(
    audit.ground_truth_id === groundTruthId,
    "Ground-truth DB audit returned wrong id.",
  );
  assert(
    audit.template_id === templateId,
    "Ground-truth DB audit returned wrong eval template id.",
  );
  assert(
    audit.organization_id === organizationId,
    "Ground-truth DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Ground-truth DB audit workspace did not match request context.",
  );
  assert(
    audit.deleted === expectedDeleted,
    "Ground-truth DB audit deleted state did not match expected state.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Ground-truth soft-delete did not set deleted_at.",
    );
  }
  assert(
    Number(audit.row_count) === 2,
    "Ground-truth DB audit row count did not match uploaded rows.",
  );
}

function assertDatasetListRow(row) {
  assert(isUuid(row.id), "Dataset list row did not expose a UUID id.");
  assert(
    typeof row.name === "string" && row.name.length > 0,
    "Dataset name missing.",
  );
  assert(
    Number.isInteger(Number(row.number_of_datapoints)),
    "Dataset list row did not expose number_of_datapoints.",
  );
  assert(
    Number.isInteger(Number(row.number_of_experiments)),
    "Dataset list row did not expose number_of_experiments.",
  );
  assert(
    Number.isInteger(Number(row.number_of_optimisations)),
    "Dataset list row did not expose number_of_optimisations.",
  );
  assert(
    Number.isInteger(Number(row.derived_datasets)),
    "Dataset list row did not expose derived_datasets.",
  );
  assert(
    typeof row.created_at === "string" && row.created_at.length > 0,
    "Dataset list row did not expose created_at.",
  );
  assert(
    typeof row.dataset_type === "string" && row.dataset_type.length > 0,
    "Dataset list row did not expose dataset_type.",
  );
}

function assertDatasetOptimizationListRows(rows) {
  assert(
    rows.length > 0,
    "Dataset optimization list returned no rows for the selected dataset.",
  );
  for (const row of rows) {
    assert(isUuid(row.id), "Dataset optimization list row missing UUID id.");
    assert(
      typeof row.optimization_name === "string" &&
        row.optimization_name.length > 0,
      "Dataset optimization list row missing optimization_name.",
    );
    assert(
      typeof row.status === "string" && row.status.length > 0,
      "Dataset optimization list row missing status.",
    );
    assert(
      typeof row.optimizer_algorithm === "string" &&
        row.optimizer_algorithm.length > 0,
      "Dataset optimization list row missing optimizer_algorithm.",
    );
    assert(
      Number.isInteger(Number(row.trial_count)),
      "Dataset optimization list row missing numeric trial_count.",
    );
    assert(
      !row.column_id || isUuid(row.column_id),
      "Dataset optimization list row returned invalid column_id.",
    );
  }
}

function assertDatasetOptimizationDetail(detail) {
  assert(
    detail && typeof detail === "object",
    "Dataset optimization detail returned an empty payload.",
  );
  assert(
    typeof detail.optimiser_name === "string" &&
      detail.optimiser_name.length > 0,
    "Dataset optimization detail missing optimiser_name.",
  );
  assert(
    typeof detail.status === "string" && detail.status.length > 0,
    "Dataset optimization detail missing status.",
  );
  assert(
    Array.isArray(detail.table),
    "Dataset optimization detail missing trial comparison table.",
  );
  assert(
    Array.isArray(detail.column_config),
    "Dataset optimization detail missing column_config.",
  );
  assert(
    Array.isArray(detail.parameters),
    "Dataset optimization detail missing parameters array.",
  );
}

function assertDatasetOptimizationSteps(stepsPayload, expectedStepCount) {
  const steps = payloadArray(stepsPayload, "steps");
  assert(
    steps.length >= Number(expectedStepCount || 0),
    "Dataset optimization steps endpoint returned fewer rows than DB audit.",
  );
  for (const step of steps) {
    assert(isUuid(step.id), "Dataset optimization step missing UUID id.");
    assert(
      typeof step.name === "string" && step.name.length > 0,
      "Dataset optimization step missing name.",
    );
    assert(
      typeof step.status === "string" && step.status.length > 0,
      "Dataset optimization step missing status.",
    );
    assert(
      Number.isInteger(Number(step.step_number)),
      "Dataset optimization step missing numeric step_number.",
    );
  }
}

function assertDatasetOptimizationGraph(graph) {
  assert(
    graph && typeof graph === "object" && !Array.isArray(graph),
    "Dataset optimization graph endpoint did not return an object.",
  );
}

function assertDatasetOptimizationTrialPayload(payload) {
  assert(
    payload && typeof payload === "object",
    "Dataset optimization trial detail returned an empty payload.",
  );
  assert(
    typeof payload.trial_name === "string" && payload.trial_name.length > 0,
    "Dataset optimization trial detail missing trial_name.",
  );
  assert(
    payload.trial && isUuid(payload.trial.id),
    "Dataset optimization trial detail missing trial UUID.",
  );
}

function assertDatasetOptimizationTrialPrompt(payload) {
  assert(
    payload && typeof payload === "object",
    "Dataset optimization trial prompt returned an empty payload.",
  );
  assert(
    typeof payload.trial_name === "string" && payload.trial_name.length > 0,
    "Dataset optimization trial prompt missing trial_name.",
  );
  assert(
    typeof payload.trial_prompt === "string" || payload.trial_prompt === null,
    "Dataset optimization trial prompt returned invalid trial_prompt.",
  );
  assert(
    typeof payload.base_prompt === "string" || payload.base_prompt === null,
    "Dataset optimization trial prompt returned invalid base_prompt.",
  );
}

function assertDatasetOptimizationTrialTable(payload) {
  assert(
    payload && typeof payload === "object",
    "Dataset optimization trial table endpoint returned an empty payload.",
  );
  assert(
    Array.isArray(payload.table),
    "Dataset optimization trial table endpoint missing table array.",
  );
  assert(
    Array.isArray(payload.column_config),
    "Dataset optimization trial table endpoint missing column_config.",
  );
  assert(
    Number.isInteger(Number(payload.total_items)),
    "Dataset optimization trial table endpoint missing numeric total_items.",
  );
}

function assertDatasetOptimizationDeletedAudit(audit) {
  assert(
    audit?.run_deleted === true,
    "Dataset optimization delete did not soft-delete the run.",
  );
  assert(
    audit.run_deleted_at_set === true,
    "Dataset optimization delete did not set run deleted_at.",
  );
  for (const key of [
    "active_step_count",
    "active_trial_count",
    "active_item_count",
    "active_evaluation_count",
  ]) {
    assert(
      Number(audit[key]) === 0,
      `Dataset optimization delete left active child rows for ${key}.`,
    );
  }
  for (const key of [
    "deleted_step_deleted_at_count",
    "deleted_trial_deleted_at_count",
    "deleted_item_deleted_at_count",
    "deleted_evaluation_deleted_at_count",
  ]) {
    assert(
      Number(audit[key]) > 0,
      `Dataset optimization delete did not set deleted_at for ${key}.`,
    );
  }
}

function assertDatasetNameRows(rows) {
  assert(
    rows.length > 0,
    "Dataset names endpoint returned no rows for search.",
  );
  for (const row of rows) {
    assert(
      isUuid(row.dataset_id),
      "Dataset names row did not expose dataset_id.",
    );
    assert(
      typeof row.name === "string",
      "Dataset names row did not expose name.",
    );
    assert(
      typeof row.model_type === "string" || row.model_type === undefined,
      "Dataset names row returned an invalid model_type.",
    );
  }
}

function assertColumnRows(rows) {
  assert(
    rows.length > 0,
    "Dataset columns endpoint returned no visible columns.",
  );
  for (const column of rows) {
    assert(isUuid(column.id), "Dataset column row did not expose a UUID id.");
    assert(
      typeof column.name === "string",
      "Dataset column row did not expose name.",
    );
    assert(
      typeof column.data_type === "string",
      "Dataset column row did not expose data_type.",
    );
  }
}

function assertAnnotationSummaryShape(summary) {
  assert(
    Array.isArray(summary?.labels),
    "Annotation summary labels must be an array.",
  );
  assert(
    Array.isArray(summary?.annotators),
    "Annotation summary annotators must be an array.",
  );
  assert(
    summary?.header && typeof summary.header === "object",
    "Annotation summary header missing.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(summary.header, "dataset_coverage"),
    "Annotation summary header missing dataset_coverage.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(summary.header, "completion_eta"),
    "Annotation summary header missing completion_eta.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(summary.header, "overall_agreement"),
    "Annotation summary header missing overall_agreement.",
  );
}

function assertRunPromptStatsShape(stats) {
  assert(
    Number.isFinite(Number(stats?.avg_tokens)),
    "Run-prompt stats did not expose avg_tokens.",
  );
  assert(
    Number.isFinite(Number(stats?.avg_cost)),
    "Run-prompt stats missing avg_cost.",
  );
  assert(
    Number.isFinite(Number(stats?.avg_time)),
    "Run-prompt stats missing avg_time.",
  );
  assert(
    Array.isArray(stats?.prompts),
    "Run-prompt stats prompts must be an array.",
  );
}

function assertJsonSchemaShape(schema) {
  assert(
    schema && typeof schema === "object" && !Array.isArray(schema),
    "Dataset JSON schema endpoint did not return an object.",
  );
  for (const [columnId, entry] of Object.entries(schema)) {
    assert(
      isUuid(columnId),
      "Dataset JSON schema entry key was not a column UUID.",
    );
    assert(
      typeof entry?.name === "string" && entry.name.length > 0,
      "Dataset JSON schema entry missing column name.",
    );
    assert(
      Array.isArray(entry.keys) ||
        Number.isInteger(entry.max_images_count) ||
        Number.isInteger(entry.max_array_count),
      "Dataset JSON schema entry did not expose keys or media/array count metadata.",
    );
  }
}

function assertExplanationSummaryShape(summary, activeRowCount) {
  assert(
    Array.isArray(summary?.response),
    "Explanation summary response must be an array.",
  );
  assert(
    typeof summary?.status === "string",
    "Explanation summary status missing.",
  );
  assert(
    Number(summary?.row_count) === Number(activeRowCount),
    "Explanation summary row_count did not match active DB row count.",
  );
  assert(
    Number.isInteger(Number(summary?.min_rows_required)),
    "Explanation summary min_rows_required missing.",
  );
}

function assertProviderStatusRows(providers, dbAudit) {
  assert(
    providers.length > 0,
    "Provider status endpoint returned no providers.",
  );
  const configured = providers.filter((provider) => provider.has_key);
  const providerNames = new Set(providers.map((provider) => provider.provider));
  const dbProviderCounts = dbAudit.provider_key_counts || {};
  const supportedDbProviders = Object.entries(dbProviderCounts).filter(
    ([provider]) => providerNames.has(provider),
  );
  const unsupportedProviders = Object.keys(dbProviderCounts).filter(
    (provider) => !providerNames.has(provider),
  );
  const duplicateProviderKeyRows = Object.values(dbProviderCounts).reduce(
    (total, count) => total + Math.max(0, Number(count) - 1),
    0,
  );
  assert(
    configured.length === supportedDbProviders.length,
    "Provider status configured count did not match supported active DB provider keys.",
  );
  for (const provider of providers) {
    assert(
      typeof provider.provider === "string",
      "Provider row missing provider.",
    );
    assert(
      typeof provider.display_name === "string",
      "Provider row missing display_name.",
    );
    assert(
      typeof provider.has_key === "boolean",
      "Provider row did not expose boolean has_key.",
    );
    assert(
      ["text", "json"].includes(provider.type),
      "Provider row type is invalid.",
    );
    if (provider.has_key) {
      assert(
        isUuid(provider.id),
        "Configured provider row did not expose key id.",
      );
      assert(
        maskedKeyStrings(provider.masked_key).some((value) =>
          value.includes("*"),
        ),
        "Configured provider did not return a masked key value.",
      );
    } else {
      assert(
        provider.id === null,
        "Unconfigured provider unexpectedly returned key id.",
      );
      assert(
        provider.masked_key === null,
        "Unconfigured provider unexpectedly returned masked_key.",
      );
    }
  }
  return { duplicateProviderKeyRows, unsupportedProviders };
}

function assertRunPromptOptions(options, providers) {
  const models = payloadArray(options, "models");
  assert(models.length > 0, "Run-prompt options returned no models.");
  assert(
    Array.isArray(options?.output_formats),
    "Run-prompt options missing formats.",
  );
  assert(
    Array.isArray(options?.tool_choices),
    "Run-prompt options missing tool choices.",
  );
  assert(
    Array.isArray(options?.available_tools),
    "Run-prompt options missing tools.",
  );
  const providerAvailability = new Map(
    providers.map((provider) => [provider.provider, provider.has_key]),
  );
  for (const model of models) {
    assert(
      typeof model.model_name === "string",
      "Run-prompt model missing name.",
    );
    assert(
      typeof model.providers === "string",
      "Run-prompt model missing provider.",
    );
    assert(
      typeof model.is_available === "boolean",
      "Run-prompt model missing boolean is_available.",
    );
    if (providerAvailability.has(model.providers)) {
      assert(
        model.is_available === providerAvailability.get(model.providers),
        `Run-prompt model availability did not match provider status for ${model.providers}.`,
      );
    }
  }
}

function selectAvailableRunPromptModel(options) {
  const models = payloadArray(options, "models").filter(
    (model) => model?.is_available && model?.model_name,
  );
  return (
    models.find((model) => model.model_name === "gpt-4o-mini") ||
    models.find((model) => model.model_name === "gpt-4.1-mini") ||
    models.find((model) => model.providers === "openai") ||
    models[0] ||
    null
  );
}

function runPromptColumnConfig(modelName, inputName) {
  return {
    model: modelName,
    messages: [
      {
        role: "system",
        content: [
          {
            type: "text",
            text: "Return exactly OK. Do not include punctuation.",
          },
        ],
      },
      {
        role: "user",
        content: [{ type: "text", text: `Input: {{${inputName}}}` }],
      },
    ],
    output_format: "string",
    max_tokens: 20,
    concurrency: 1,
    run_prompt_config: {
      temperature: 0,
      max_tokens: 20,
      template_format: "f-string",
    },
  };
}

function assertRunPromptPreview(preview) {
  assert(
    Array.isArray(preview?.responses) && preview.responses.length === 1,
    "Run-prompt preview did not return one response.",
  );
  assert(
    String(preview.responses[0] || "").trim().length > 0,
    "Run-prompt preview response was empty.",
  );
  assert(
    preview.token_usage && typeof preview.token_usage === "object",
    "Run-prompt preview did not return token_usage metadata.",
  );
  assert(
    preview.cost && typeof preview.cost === "object",
    "Run-prompt preview did not return cost metadata.",
  );
}

function assertRunPromptConfigReload(
  payload,
  { datasetId, outputName, modelName, inputName },
) {
  const config = payload?.config || payload;
  assert(
    config?.dataset_id === datasetId,
    "Run-prompt config dataset mismatch.",
  );
  assert(config?.name === outputName, "Run-prompt config name mismatch.");
  assert(config?.model === modelName, "Run-prompt config model mismatch.");
  const messageText = JSON.stringify(config?.messages || []);
  assert(
    messageText.includes(inputName),
    "Run-prompt config reload did not preserve the input placeholder.",
  );
}

async function waitForRunPromptColumnCompletion(
  client,
  { datasetId, rowId, columnName, timeoutMs = 180000, intervalMs = 3000 },
) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = "missing";
  while (Date.now() < deadline) {
    const table = await getDatasetTable(client, datasetId, { page_size: 100 });
    const column = findColumn(table, columnName);
    const row = asArray(table?.table).find(
      (candidate) => candidate.row_id === rowId,
    );
    const cell = column?.id && row ? row[column.id] : null;
    const status = String(
      cell?.cell_status || cell?.status || cell?.value_status || "",
    ).toLowerCase();
    lastStatus = status || (column ? "waiting-for-cell" : "waiting-for-column");
    if (column?.id && cell && ["pass", "error"].includes(status)) {
      return {
        table,
        column,
        row,
        cell,
        status,
        value: cell.cell_value,
      };
    }
    await sleep(intervalMs);
  }
  throw new Error(
    `Timed out waiting for run-prompt column ${columnName}; last status=${lastStatus}.`,
  );
}

async function expectApiErrorStatus(fn, status, message) {
  try {
    await fn();
  } catch (error) {
    assert(
      error?.status === status,
      `${message} Expected HTTP ${status}, got ${error?.status || error.message}.`,
    );
    return error;
  }
  throw new Error(message);
}

async function expectLegacyKnowledgeBaseEntitlementDenied(fn, message) {
  try {
    await fn();
  } catch (error) {
    assert(
      isLegacyKnowledgeBaseEntitlementDeniedError(error),
      `${message} Expected HTTP 402 knowledge_base entitlement metadata, got ${
        error?.status || error.message
      }: ${JSON.stringify(error?.body || {})}`,
    );
    return error;
  }
  throw new Error(message);
}

async function skipIfLegacyKnowledgeBaseEntitlementDenied(client, evidence) {
  try {
    await client.get(apiPath("/model-hub/knowledge-base/get/"), {
      query: { page_number: 0, page_size: 1 },
    });
  } catch (error) {
    if (isLegacyKnowledgeBaseEntitlementDeniedError(error)) {
      evidence.push({
        mode: "legacy_entitlement_denied",
        status: error.status,
        body: error.body,
      });
      skip(
        "Legacy knowledge-base endpoints are entitlement-blocked in this local workspace; KB-API-004 covers the current gate.",
      );
    }
    throw error;
  }
}

function isLegacyKnowledgeBaseEntitlementDeniedError(error) {
  const text = JSON.stringify(error?.body || {}).toLowerCase();
  return (
    error?.status === 402 &&
    (text.includes("entitlement") ||
      text.includes("upgrade") ||
      text.includes("knowledge_base"))
  );
}

function assertFunctionRows(functions) {
  for (const fn of functions) {
    assert(
      typeof fn.description === "string",
      "Function list row missing description.",
    );
    assert(
      fn.config && typeof fn.config === "object",
      "Function list row missing config object.",
    );
  }
}

function assertAdvancedDatasetReadbacks(
  rowData,
  cellData,
  table,
  {
    rowId,
    renamedTextColumn,
    multiBoolColumn,
    addIntegerColumn,
    addJsonColumn,
    textValue,
    jsonValue,
  },
) {
  assert(
    rowData?.current?.row_id === rowId,
    "get-row-data returned the wrong current row.",
  );
  assert(
    rowData.current?.[renamedTextColumn.id]?.cell_value === textValue,
    "get-row-data did not return the text cell value.",
  );
  assert(
    rowData.current?.[multiBoolColumn.id]?.cell_value === "true",
    "get-row-data did not return the boolean cell value.",
  );
  assert(
    String(rowData.current?.[addIntegerColumn.id]?.cell_value) === "42",
    "get-row-data did not return the edited integer cell value.",
  );
  assert(
    jsonValuesEqual(rowData.current?.[addJsonColumn.id]?.cell_value, jsonValue),
    "get-row-data did not return the JSON cell value.",
  );
  assert(
    Array.isArray(rowData?.next?.row_id),
    "get-row-data did not expose next row ids.",
  );

  const rowCells = cellData?.[rowId] || {};
  assert(
    rowCells[renamedTextColumn.id]?.cell_value === textValue,
    "get-cell-data did not return the text cell value.",
  );
  assert(
    String(rowCells[addIntegerColumn.id]?.cell_value) === "42",
    "get-cell-data did not return the edited integer value.",
  );
  assert(
    jsonValuesEqual(rowCells[addJsonColumn.id]?.cell_value, jsonValue),
    "get-cell-data did not return the JSON value.",
  );

  const tableRow = asArray(table?.table).find((row) => row.row_id === rowId);
  assert(tableRow, "get-dataset-table did not include the added row.");
  assert(
    cellValueFor(tableRow, renamedTextColumn.id) === textValue,
    "Dataset table did not return the text value.",
  );
  assert(
    String(cellValueFor(tableRow, addIntegerColumn.id)) === "42",
    "Dataset table did not return the edited integer value.",
  );
}

function assertDatasetRowColumnLifecycleDbAudit(
  audit,
  {
    datasetId,
    organizationId,
    workspaceId,
    expectedDeleted,
    rowCount,
    columnCount,
  },
) {
  assert(
    audit.dataset_id === datasetId,
    "Dataset lifecycle DB audit returned a different dataset id.",
  );
  assert(
    audit.organization_id === organizationId,
    "Dataset lifecycle DB audit organization did not match request context.",
  );
  assert(
    !workspaceId || audit.workspace_id === workspaceId,
    "Dataset lifecycle DB audit workspace did not match request context.",
  );
  if (expectedDeleted) {
    assert(
      Number(audit.active_temp_rows) === 0,
      "Dataset lifecycle DB audit found active temp rows after cleanup.",
    );
    assert(
      Number(audit.active_temp_columns) === 0,
      "Dataset lifecycle DB audit found active temp columns after cleanup.",
    );
    assert(
      Number(audit.active_temp_cells) === 0,
      "Dataset lifecycle DB audit found active temp cells after cleanup.",
    );
    assert(
      Number(audit.deleted_temp_rows) === rowCount &&
        Number(audit.deleted_row_deleted_at_count) === rowCount,
      "Deleted dataset rows were not all soft-deleted with deleted_at.",
    );
    assert(
      Number(audit.deleted_temp_columns) === columnCount &&
        Number(audit.deleted_column_deleted_at_count) === columnCount,
      "Deleted dataset columns were not all soft-deleted with deleted_at.",
    );
    assert(
      Number(audit.deleted_temp_cells) >= columnCount &&
        Number(audit.deleted_cell_deleted_at_count) ===
          Number(audit.deleted_temp_cells),
      "Deleted dataset cells were not all soft-deleted with deleted_at.",
    );
    assert(
      Number(audit.column_order_contains_deleted_count) === 0,
      "Deleted dataset columns remained in column_order.",
    );
    assert(
      Number(audit.column_config_contains_deleted_count) === 0,
      "Deleted dataset columns remained in column_config.",
    );
  } else {
    assert(
      Number(audit.active_temp_rows) === rowCount,
      "Dataset lifecycle DB audit did not find the active temp row.",
    );
    assert(
      Number(audit.active_temp_columns) === columnCount,
      "Dataset lifecycle DB audit did not find all active temp columns.",
    );
    assert(
      Number(audit.active_temp_cells) >= columnCount,
      "Dataset lifecycle DB audit did not find expected active temp cells.",
    );
  }
}

function assertRunPromptColumnDbAudit(
  audit,
  {
    datasetId,
    rowId,
    columnId,
    outputName,
    modelName,
    organizationId,
    workspaceId,
    expectedDeleted,
  },
) {
  assert(rowId, "Run-prompt DB audit expected a row id.");
  assert(audit?.dataset_id === datasetId, "Run-prompt DB dataset mismatch.");
  assert(audit.column_id === columnId, "Run-prompt DB column mismatch.");
  assert(
    audit.column_name === outputName,
    "Run-prompt DB column name mismatch.",
  );
  assert(
    audit.run_prompt_dataset_id === datasetId,
    "Run-prompt DB run prompt points at the wrong dataset.",
  );
  assert(
    audit.run_prompt_organization_id === organizationId,
    "Run-prompt DB run prompt organization mismatch.",
  );
  assert(
    audit.organization_id === organizationId,
    "Run-prompt DB dataset organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Run-prompt DB dataset workspace mismatch.",
    );
    assert(
      audit.run_prompt_workspace_id === workspaceId,
      "Run-prompt DB run prompt workspace mismatch.",
    );
  }
  assert(
    audit.run_prompt_model === modelName,
    "Run-prompt DB model did not match the executed model.",
  );
  if (expectedDeleted) {
    assert(
      audit.column_deleted === true,
      "Run-prompt output column not deleted.",
    );
    assert(
      audit.column_deleted_at_set === true,
      "Run-prompt output column missing deleted_at.",
    );
    assert(audit.run_prompt_deleted === true, "RunPrompter was not deleted.");
    assert(
      audit.run_prompt_deleted_at_set === true,
      "RunPrompter missing deleted_at.",
    );
    assert(
      Number(audit.active_cell_count) === 0,
      "Run-prompt DB found active output cells after cleanup.",
    );
    assert(
      Number(audit.deleted_cell_count) >= 1 &&
        Number(audit.deleted_cell_count) ===
          Number(audit.deleted_cell_deleted_at_count),
      "Run-prompt output cells were not soft-deleted with deleted_at.",
    );
  } else {
    assert(
      audit.column_deleted === false,
      "Run-prompt output column is deleted.",
    );
    assert(audit.run_prompt_deleted === false, "RunPrompter is deleted.");
    assert(
      ["Completed", "Running", "Failed"].includes(audit.run_prompt_status),
      "RunPrompter status was not a known execution status.",
    );
    assert(
      Number(audit.active_cell_count) === 1,
      "Run-prompt DB did not find the active output cell.",
    );
  }
}

function assertDatasetSdkSnippetSafety(result) {
  const expectedCodeKeys = [
    "curl_add_col",
    "curl_add_row",
    "python_add_col",
    "python_add_row",
    "typescript_add_col",
    "typescript_add_row",
  ];
  const code = result?.code || {};
  for (const key of expectedCodeKeys) {
    assert(
      typeof code[key] === "string" && code[key].length > 0,
      `add_rows_sdk missing non-empty code.${key}.`,
    );
  }

  assert(
    result?.api_keys?.api_key === "YOUR_API_KEY",
    "add_rows_sdk exposed a non-placeholder API key.",
  );
  assert(
    result?.api_keys?.secret_key === "YOUR_SECRET_KEY",
    "add_rows_sdk exposed a non-placeholder secret key.",
  );

  const snippets = Object.values(code).join("\n");
  assert(
    snippets.includes("YOUR_API_KEY") && snippets.includes("YOUR_SECRET_KEY"),
    "SDK snippets did not include placeholder credentials.",
  );
  const rawCredentialPatterns = [
    /FI_API_KEY"\]\s*=\s*"[0-9a-f]{32}"/i,
    /FI_SECRET_KEY"\]\s*=\s*"[0-9a-f]{32}"/i,
    /X-Api-Key:\s*[0-9a-f]{32}/i,
    /X-Secret-Key:\s*[0-9a-f]{32}/i,
  ];
  assert(
    !rawCredentialPatterns.some((pattern) => pattern.test(snippets)),
    "SDK snippets included a raw generated credential value.",
  );
}

function assertKnowledgeBaseSdkSnippetSafety(code, mode) {
  assert(
    typeof code === "string" && code.length > 0,
    `knowledge-base ${mode} SDK code was empty.`,
  );
  assert(
    code.includes("YOUR_API_KEY") && code.includes("YOUR_SECRET_KEY"),
    `knowledge-base ${mode} SDK code did not include placeholder credentials.`,
  );
  const rawCredentialPatterns = [
    /FI_API_KEY="?[0-9a-f]{32}"?/i,
    /FI_SECRET_KEY="?[0-9a-f]{32}"?/i,
    /fi_api_key\s*=\s*"[0-9a-f]{32}"/i,
    /fi_secret_key\s*=\s*"[0-9a-f]{32}"/i,
  ];
  assert(
    !rawCredentialPatterns.some((pattern) => pattern.test(code)),
    `knowledge-base ${mode} SDK code included a raw generated credential value.`,
  );
}

function assertKnowledgeBaseTableRows(rows, kbId, kbName) {
  const row = rows.find((item) => item?.id === kbId);
  assert(row, "Knowledge base table endpoint did not return the created KB.");
  assert(
    row.name === kbName,
    "Knowledge base table returned the wrong KB name.",
  );
  assert(
    Number(row.files_uploaded) >= 1,
    "Knowledge base table did not report the uploaded file.",
  );
  assert(
    typeof row.status === "string" && row.status.length > 0,
    "Knowledge base table row was missing status.",
  );
}

function assertKnowledgeBaseFileRows(rows, fileId, fileName) {
  const row = rows.find((item) => item?.id === fileId);
  assert(
    row,
    "Knowledge base files endpoint did not return the uploaded file.",
  );
  assert(
    row.name === fileName,
    "Knowledge base files endpoint returned wrong name.",
  );
  assert(
    Number(row.file_size) > 0,
    "Knowledge base files endpoint did not return file size.",
  );
  assert(
    typeof row.status === "string" && row.status.length > 0,
    "Knowledge base files endpoint was missing file status.",
  );
}

function assertKnowledgeBaseLegacyDbAudit(
  audit,
  {
    kbId,
    kbName,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedFileIds,
  },
) {
  assert(
    audit?.kb_id === kbId,
    "Knowledge base DB audit returned wrong KB id.",
  );
  assert(
    audit.name === kbName,
    "Knowledge base DB audit returned wrong KB name.",
  );
  assert(
    audit.organization_id === organizationId,
    "Knowledge base DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Knowledge base DB audit workspace mismatch.",
    );
  }
  assert(
    audit.deleted === expectedDeleted,
    "Knowledge base DB audit deleted state mismatch.",
  );
  assert(
    Number(audit.file_count) === expectedFileIds.length,
    "Knowledge base DB audit file count mismatch.",
  );
  for (const fileId of expectedFileIds) {
    assert(
      audit.file_ids.includes(fileId),
      "Knowledge base DB audit was missing the uploaded file id.",
    );
  }
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Knowledge base soft delete did not stamp deleted_at.",
    );
  } else {
    assert(
      audit.deleted_at_set === false,
      "Active knowledge base unexpectedly had deleted_at set.",
    );
  }
}

function assertStructuredEmbeddingModels(models) {
  assert(
    Array.isArray(models),
    "Structured KB embedding model list was not an array.",
  );
  assert(
    models.some((row) => row?.value === "BAAI/bge-small-en-v1.5"),
    "Structured KB embedding model list did not include the default BGE model.",
  );
  for (const row of models) {
    assert(
      typeof row.value === "string" && typeof row.label === "string",
      "Structured KB embedding model row was missing value/label.",
    );
  }
}

function assertStructuredKnowledgeBasePayload(
  payload,
  { kbId, name, embeddingModel, chunkSize },
) {
  assert(payload?.id === kbId, "Structured KB payload returned the wrong id.");
  assert(
    payload.name === name,
    "Structured KB payload returned the wrong name.",
  );
  assert(
    payload.embedding_model === embeddingModel,
    "Structured KB payload returned the wrong embedding model.",
  );
  assert(
    Number(payload.chunk_size) === Number(chunkSize),
    "Structured KB payload returned the wrong chunk size.",
  );
}

function assertStructuredKnowledgeBaseDbAudit(
  audit,
  {
    kbId,
    name,
    embeddingModel,
    chunkSize,
    organizationId,
    workspaceId,
    expectedDeleted,
  },
) {
  assert(audit?.kb_id === kbId, "Structured KB DB audit returned wrong id.");
  assert(audit.name === name, "Structured KB DB audit returned wrong name.");
  assert(
    audit.embedding_model === embeddingModel,
    "Structured KB DB audit returned wrong embedding model.",
  );
  assert(
    Number(audit.chunk_size) === Number(chunkSize),
    "Structured KB DB audit returned wrong chunk size.",
  );
  assert(
    audit.organization_id === organizationId,
    "Structured KB DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Structured KB DB audit workspace mismatch.",
    );
  }
  assert(
    audit.deleted === expectedDeleted,
    "Structured KB DB audit deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Structured KB delete did not stamp deleted_at.",
    );
  } else {
    assert(
      audit.deleted_at_set === false,
      "Structured KB active row unexpectedly had deleted_at set.",
    );
  }
}

function assertEvalSdkSnippetSafety(result) {
  const expectedCodeKeys = ["curl", "javascript", "python"];
  for (const key of expectedCodeKeys) {
    assert(
      typeof result?.[key] === "string" && result[key].length > 0,
      `eval-sdk-code missing non-empty ${key} snippet.`,
    );
  }

  const snippets = expectedCodeKeys.map((key) => result[key]).join("\n");
  assert(
    snippets.includes("YOUR_API_KEY") && snippets.includes("YOUR_SECRET_KEY"),
    "eval SDK snippets did not include placeholder credentials.",
  );
  const rawCredentialPatterns = [
    /fi_api_key\s*=\s*"?[0-9a-f]{32}"?/i,
    /fi_secret_key\s*=\s*"?[0-9a-f]{32}"?/i,
    /X-Api-Key['"]?\s*:\s*['"]?[0-9a-f]{32}/i,
    /X-Secret-Key['"]?\s*:\s*['"]?[0-9a-f]{32}/i,
  ];
  assert(
    !rawCredentialPatterns.some((pattern) => pattern.test(snippets)),
    "eval SDK snippets included a raw generated credential value.",
  );
}

function assertSortedDescending(values, message) {
  for (let index = 1; index < values.length; index += 1) {
    assert(String(values[index - 1]) >= String(values[index]), message);
  }
}

function payloadArray(payload, key) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.[key])) return payload[key];
  return asArray(payload);
}

function assertDatasetComparePayload(payload) {
  assert(
    payload && typeof payload === "object",
    "Dataset compare returned an empty payload.",
  );
  assert(
    isUuid(payload?.metadata?.compare_id || payload?.compare_id),
    "Dataset compare payload missing compare_id.",
  );
  assert(
    Number(payload?.metadata?.total_rows || 0) > 0,
    "Dataset compare payload reported no rows.",
  );
  assert(
    Number(payload?.metadata?.total_pages || 0) > 0,
    "Dataset compare payload reported no pages.",
  );
  assert(
    asArray(payload.table).length > 0,
    "Dataset compare payload did not return table rows.",
  );
  assert(
    asArray(payload.column_config).length > 0,
    "Dataset compare payload did not return column_config.",
  );
}

function assertDatasetCompareRowPayload(payload, expectedRowId) {
  const rows = asArray(payload?.table);
  assert(rows.length === 1, "Compare row detail did not return one row.");
  assert(
    rows[0]?.row_id === expectedRowId,
    "Compare row detail returned a different row id.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "prev_row_id"),
    "Compare row detail missing prev_row_id.",
  );
  assert(
    Object.prototype.hasOwnProperty.call(payload, "next_row_id"),
    "Compare row detail missing next_row_id.",
  );
}

function assertDatasetCopyFixtureAudit(audit) {
  assert(
    Number(audit?.source_row_count) === 4,
    "Dataset copy DB audit source row count mismatch.",
  );
  assert(
    Number(audit?.source_max_order) === 3,
    "Dataset copy DB audit source max order mismatch.",
  );
  assert(
    Number(audit?.source_duplicate_value_count) === 3,
    "Dataset copy DB audit did not find the duplicated source rows.",
  );
  assert(
    Number(audit?.target_row_count) === 7,
    "Dataset copy DB audit target row count mismatch.",
  );
  assert(
    Number(audit?.target_max_order) === 15,
    "Dataset copy DB audit target max order mismatch.",
  );
  assert(
    audit?.target_order_11_input === audit?.source_input_two_value,
    "Dataset merge did not append the selected row after the highest target order.",
  );
  assert(
    Number(audit?.target_source_one_count) === 3,
    "Dataset import did not copy all duplicated source-one rows.",
  );
  assert(
    Number(audit?.target_source_two_count) === 2,
    "Dataset merge/import source-two row count mismatch.",
  );
  assert(
    Number(audit?.target_order_12_15_count) === 4,
    "Dataset import did not append rows after the merged row.",
  );
}

function maskedKeyStrings(value) {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(maskedKeyStrings);
  if (value && typeof value === "object") {
    return Object.values(value).flatMap(maskedKeyStrings);
  }
  return [];
}

function jsonValuesEqual(actual, expected) {
  try {
    return (
      JSON.stringify(JSON.parse(String(actual))) ===
      JSON.stringify(JSON.parse(String(expected)))
    );
  } catch {
    return false;
  }
}

async function loadDatasetMetadataDbAudit(
  datasetId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH dataset_row AS (
  SELECT id, name, organization_id, workspace_id, column_order
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(datasetId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
)
SELECT json_build_object(
  'dataset_id', d.id::text,
  'dataset_name', d.name,
  'organization_id', d.organization_id::text,
  'workspace_id', d.workspace_id::text,
  'active_row_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.dataset_id = d.id AND r.deleted = false
  ),
  'visible_other_column_count', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.dataset_id = d.id
      AND c.deleted = false
      AND c.source = 'OTHERS'
      AND c.id::text = ANY(d.column_order)
  ),
  'base_column_name_count', (
    SELECT count(DISTINCT c.name)
    FROM model_hub_column c
    WHERE c.dataset_id = d.id
      AND c.deleted = false
      AND c.source NOT IN (
        'experiment',
        'experiment_evaluation_tags',
        'evaluation_tags',
        'optimisation_evaluation_tags',
        'evaluation',
        'experiment_evaluation',
        'optimisation_evaluation',
        'evaluation_reason'
      )
  ),
  'run_prompt_count', (
    SELECT count(*)
    FROM model_hub_runprompter rp
    WHERE rp.dataset_id = d.id AND rp.deleted = false
  ),
  'user_eval_metric_count', (
    SELECT count(*)
    FROM model_hub_userevalmetric uem
    WHERE uem.dataset_id = d.id
      AND uem.deleted = false
      AND uem.show_in_sidebar = true
  ),
  'provider_key_count', (
    SELECT count(*)
    FROM model_hub_apikey ak
    WHERE ak.organization_id = ${sqlUuid(organizationId)}
      AND ak.deleted = false
  ),
  'provider_key_counts', (
    SELECT COALESCE(json_object_agg(provider_counts.provider, provider_counts.count), '{}'::json)
    FROM (
      SELECT ak.provider, count(*) AS count
      FROM model_hub_apikey ak
      WHERE ak.organization_id = ${sqlUuid(organizationId)}
        AND ak.deleted = false
      GROUP BY ak.provider
    ) provider_counts
  )
)
FROM dataset_row d;
`;
  return runPostgresJson(sql);
}

async function seedDatasetCompareFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const baseDatasetId = randomUUID();
  const otherDatasetId = randomUUID();
  const baseInputColumnId = randomUUID();
  const baseAnswerColumnId = randomUUID();
  const baseJudgeColumnId = randomUUID();
  const otherInputColumnId = randomUUID();
  const otherAnswerColumnId = randomUUID();
  const otherJudgeColumnId = randomUUID();
  const rowPairs = [0, 1, 2].map((index) => ({
    input: `api compare ${suffix} row ${index + 1}`,
    baseRowId: randomUUID(),
    otherRowId: randomUUID(),
    baseAnswer: `base answer ${index + 1} ${suffix}`,
    otherAnswer: `other answer ${index + 1} ${suffix}`,
    baseJudge: index % 2 === 0 ? "pass" : "fail",
    otherJudge: index % 2 === 0 ? "fail" : "pass",
  }));
  const datasetRows = [
    {
      id: baseDatasetId,
      name: `api journey compare base ${runId}`,
      columnIds: [baseInputColumnId, baseAnswerColumnId, baseJudgeColumnId],
    },
    {
      id: otherDatasetId,
      name: `api journey compare other ${runId}`,
      columnIds: [otherInputColumnId, otherAnswerColumnId, otherJudgeColumnId],
    },
  ];
  const columnRows = [
    [baseInputColumnId, "input", "text", "OTHERS", null, baseDatasetId],
    [baseAnswerColumnId, "answer", "text", "OTHERS", null, baseDatasetId],
    [baseJudgeColumnId, "judge", "text", "evaluation", null, baseDatasetId],
    [otherInputColumnId, "input", "text", "OTHERS", null, otherDatasetId],
    [otherAnswerColumnId, "answer", "text", "OTHERS", null, otherDatasetId],
    [otherJudgeColumnId, "judge", "text", "evaluation", null, otherDatasetId],
  ];
  const rowRows = rowPairs.flatMap((rowPair, index) => [
    [rowPair.baseRowId, index, baseDatasetId],
    [rowPair.otherRowId, index, otherDatasetId],
  ]);
  const cellRows = rowPairs.flatMap((rowPair) => [
    [
      randomUUID(),
      rowPair.input,
      baseInputColumnId,
      baseDatasetId,
      rowPair.baseRowId,
    ],
    [
      randomUUID(),
      rowPair.baseAnswer,
      baseAnswerColumnId,
      baseDatasetId,
      rowPair.baseRowId,
    ],
    [
      randomUUID(),
      rowPair.baseJudge,
      baseJudgeColumnId,
      baseDatasetId,
      rowPair.baseRowId,
    ],
    [
      randomUUID(),
      rowPair.input,
      otherInputColumnId,
      otherDatasetId,
      rowPair.otherRowId,
    ],
    [
      randomUUID(),
      rowPair.otherAnswer,
      otherAnswerColumnId,
      otherDatasetId,
      rowPair.otherRowId,
    ],
    [
      randomUUID(),
      rowPair.otherJudge,
      otherJudgeColumnId,
      otherDatasetId,
      rowPair.otherRowId,
    ],
  ]);
  const datasetValues = datasetRows
    .map(
      (row) => `(
    ${sqlUuid(row.id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(row.name)},
    ARRAY[${row.columnIds.map((id) => sqlTextLiteral(id)).join(", ")}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )`,
    )
    .join(",\n");
  const columnValues = columnRows
    .map(
      ([id, name, dataType, source, sourceId, datasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    ${sqlTextLiteral(dataType)},
    ${sqlTextLiteral(source)},
    ${sourceId ? sqlTextLiteral(sourceId) : "NULL::varchar"},
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )`,
    )
    .join(",\n");
  const rowValues = rowRows
    .map(
      ([id, order, datasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${Number(order)},
    ${sqlUuid(datasetId)},
    '{}'::jsonb
  )`,
    )
    .join(",\n");
  const cellValues = cellRows
    .map(
      ([id, value, columnId, datasetId, rowId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(value)},
    ${sqlUuid(columnId)},
    ${sqlUuid(datasetId)},
    ${sqlUuid(rowId)},
    'pass',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
${datasetValues}
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
${columnValues}
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_datasets) = 2,
  'base_dataset_id', ${sqlUuid(baseDatasetId)}::text,
  'base_dataset_name', ${sqlTextLiteral(datasetRows[0].name)},
  'other_dataset_id', ${sqlUuid(otherDatasetId)}::text,
  'other_dataset_name', ${sqlTextLiteral(datasetRows[1].name)},
  'base_column_name', 'input',
  'shared_value_count', ${rowPairs.length},
  'common_column_count', 2,
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells),
  'eval_template_id', (
    SELECT et.id::text
    FROM model_hub_evaltemplate et
    WHERE et.deleted = false
      AND (et.organization_id = ${sqlUuid(organizationId)} OR et.owner = 'SYSTEM')
    ORDER BY CASE WHEN et.owner = 'SYSTEM' THEN 0 ELSE 1 END, et.created_at DESC
    LIMIT 1
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteDatasetCompareFixture(datasetIds) {
  assert(
    Array.isArray(datasetIds) && datasetIds.every(isUuid),
    "datasetIds must be UUIDs for DB cleanup.",
  );
  const sql = `
WITH deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
)
SELECT json_build_object(
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells)
);
`;
  return runPostgresJson(sql);
}

async function findOtherWorkspaceId(organizationId, workspaceId) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const activeWorkspaceFilter = workspaceId
    ? `AND id <> ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
SELECT COALESCE((
  SELECT json_build_object('workspace_id', id::text)
  FROM accounts_workspace
  WHERE organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
    ${activeWorkspaceFilter}
  ORDER BY is_default ASC, created_at DESC
  LIMIT 1
), '{}'::json);
`;
  const result = await runPostgresJson(sql);
  return result?.workspace_id || null;
}

async function seedExperimentDatasetMaterializeFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const activeWorkspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const otherWorkspaceId = await findOtherWorkspaceId(
    organizationId,
    workspaceId,
  );
  const datasetId = randomUUID();
  const duplicateDatasetId = randomUUID();
  const experimentId = randomUUID();
  const experimentDatasetId = randomUUID();
  const inputColumnId = randomUUID();
  const resultColumnId = randomUUID();
  const firstRowId = randomUUID();
  const secondRowId = randomUUID();
  const otherDatasetId = otherWorkspaceId ? randomUUID() : null;
  const otherExperimentId = otherWorkspaceId ? randomUUID() : null;
  const otherExperimentDatasetId = otherWorkspaceId ? randomUUID() : null;
  const otherInputColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherResultColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherRowId = otherWorkspaceId ? randomUUID() : null;
  const datasetName = `api journey exp materialize source ${runId}`;
  const experimentDatasetName = `api journey exp materialize result ${runId}`;
  const duplicateDatasetName = `api journey exp materialize duplicate ${runId}`;
  const otherDatasetName = `api journey exp materialize other ${runId}`;
  const otherBlockedDatasetName = `api journey exp materialize blocked ${runId}`;
  const firstInputValue = `first input ${suffix}`;
  const firstResultValue = `first result ${suffix}`;
  const secondInputValue = `second input ${suffix}`;
  const secondResultValue = `second result ${suffix}`;

  const datasetRows = [
    {
      id: datasetId,
      name: datasetName,
      workspaceSql: activeWorkspaceSql,
      columnIds: [inputColumnId, resultColumnId],
    },
    {
      id: duplicateDatasetId,
      name: duplicateDatasetName,
      workspaceSql: activeWorkspaceSql,
      columnIds: [],
    },
  ];
  if (otherWorkspaceId) {
    datasetRows.push({
      id: otherDatasetId,
      name: otherDatasetName,
      workspaceSql: sqlUuid(otherWorkspaceId),
      columnIds: [otherInputColumnId, otherResultColumnId],
    });
  }

  const datasetValues = datasetRows
    .map((row) => {
      const columnOrderSql = row.columnIds.length
        ? `${sqlTextArray(row.columnIds)}::varchar[]`
        : "ARRAY[]::varchar[]";
      return `(
    ${sqlUuid(row.id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(row.name)},
    ${columnOrderSql},
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${row.workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )`;
    })
    .join(",\n");

  const columnRows = [
    [inputColumnId, "api_exp_input", datasetId],
    [resultColumnId, "api_exp_result", datasetId],
  ];
  if (otherWorkspaceId) {
    columnRows.push(
      [otherInputColumnId, "api_exp_input", otherDatasetId],
      [otherResultColumnId, "api_exp_result", otherDatasetId],
    );
  }
  const columnValues = columnRows
    .map(
      ([id, name, columnDatasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(columnDatasetId)},
    '{}'::jsonb,
    'Completed'
  )`,
    )
    .join(",\n");

  const rowRows = [
    [firstRowId, datasetId, 0],
    [secondRowId, datasetId, 7],
  ];
  if (otherWorkspaceId) rowRows.push([otherRowId, otherDatasetId, 0]);
  const rowValues = rowRows
    .map(
      ([id, rowDatasetId, order]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${Number(order)},
    ${sqlUuid(rowDatasetId)},
    '{}'::jsonb
  )`,
    )
    .join(",\n");

  const cellRows = [
    [randomUUID(), inputColumnId, datasetId, firstRowId, firstInputValue],
    [randomUUID(), resultColumnId, datasetId, firstRowId, firstResultValue],
    [randomUUID(), inputColumnId, datasetId, secondRowId, secondInputValue],
    [randomUUID(), resultColumnId, datasetId, secondRowId, secondResultValue],
  ];
  if (otherWorkspaceId) {
    cellRows.push(
      [
        randomUUID(),
        otherInputColumnId,
        otherDatasetId,
        otherRowId,
        `other input ${suffix}`,
      ],
      [
        randomUUID(),
        otherResultColumnId,
        otherDatasetId,
        otherRowId,
        `other result ${suffix}`,
      ],
    );
  }
  const cellValues = cellRows
    .map(
      ([id, columnId, cellDatasetId, rowId, value]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(value)},
    ${sqlUuid(columnId)},
    ${sqlUuid(cellDatasetId)},
    ${sqlUuid(rowId)},
    'pass',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )`,
    )
    .join(",\n");

  const experimentRows = [
    [experimentId, `${datasetName} experiment`, datasetId, inputColumnId, datasetId],
  ];
  if (otherWorkspaceId) {
    experimentRows.push([
      otherExperimentId,
      `${otherDatasetName} experiment`,
      otherDatasetId,
      otherInputColumnId,
      otherDatasetId,
    ]);
  }
  const experimentValues = experimentRows
    .map(
      ([id, name, experimentDatasetIdForRow, columnId, snapshotDatasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    '[]'::jsonb,
    'Completed',
    ${sqlUuid(experimentDatasetIdForRow)},
    ${sqlUuid(columnId)},
    NULL::uuid,
    'llm',
    ${sqlUuid(snapshotDatasetId)}
  )`,
    )
    .join(",\n");

  const experimentDatasetRows = [
    [experimentDatasetId, experimentDatasetName, experimentId],
  ];
  if (otherWorkspaceId) {
    experimentDatasetRows.push([
      otherExperimentDatasetId,
      `${experimentDatasetName} other`,
      otherExperimentId,
    ]);
  }
  const experimentDatasetValues = experimentDatasetRows
    .map(
      ([id, name, experimentIdForRow]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    '{}'::jsonb,
    '{}'::jsonb,
    'Completed',
    ${sqlUuid(experimentIdForRow)}
  )`,
    )
    .join(",\n");

  const experimentDatasetColumnRows = [[experimentDatasetId, resultColumnId]];
  if (otherWorkspaceId) {
    experimentDatasetColumnRows.push([
      otherExperimentDatasetId,
      otherResultColumnId,
    ]);
  }
  const experimentDatasetColumnValues = experimentDatasetColumnRows
    .map(([edtId, columnId]) => `(${sqlUuid(edtId)}, ${sqlUuid(columnId)})`)
    .join(",\n");

  const experimentDatasetM2mRows = [[experimentId, experimentDatasetId]];
  if (otherWorkspaceId) {
    experimentDatasetM2mRows.push([
      otherExperimentId,
      otherExperimentDatasetId,
    ]);
  }
  const experimentDatasetM2mValues = experimentDatasetM2mRows
    .map(([expId, edtId]) => `(${sqlUuid(expId)}, ${sqlUuid(edtId)})`)
    .join(",\n");

  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
${datasetValues}
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
${columnValues}
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellValues}
  RETURNING id
),
inserted_experiments AS (
  INSERT INTO model_hub_experimentstable (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    prompt_config,
    status,
    dataset_id,
    column_id,
    user_id,
    experiment_type,
    snapshot_dataset_id
  )
  VALUES
${experimentValues}
  RETURNING id
),
inserted_experiment_datasets AS (
  INSERT INTO model_hub_experimentdatasettable (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    prompt_config,
    exclude_values,
    status,
    experiment_id
  )
  VALUES
${experimentDatasetValues}
  RETURNING id
),
inserted_experiment_dataset_columns AS (
  INSERT INTO model_hub_experimentdatasettable_columns (
    experimentdatasettable_id,
    column_id
  )
  VALUES
${experimentDatasetColumnValues}
  RETURNING id
),
inserted_experiment_dataset_m2m AS (
  INSERT INTO model_hub_experimentstable_experiments_datasets (
    experimentstable_id,
    experimentdatasettable_id
  )
  VALUES
${experimentDatasetM2mValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_experiments) >= 1,
  'organization_id', ${sqlUuid(organizationId)}::text,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'duplicate_dataset_id', ${sqlUuid(duplicateDatasetId)}::text,
  'duplicate_dataset_name', ${sqlTextLiteral(duplicateDatasetName)},
  'experiment_id', ${sqlUuid(experimentId)}::text,
  'experiment_name', ${sqlTextLiteral(`${datasetName} experiment`)},
  'experiment_dataset_id', ${sqlUuid(experimentDatasetId)}::text,
  'experiment_dataset_name', ${sqlTextLiteral(experimentDatasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'result_column_id', ${sqlUuid(resultColumnId)}::text,
  'first_row_id', ${sqlUuid(firstRowId)}::text,
  'first_input_value', ${sqlTextLiteral(firstInputValue)},
  'first_result_value', ${sqlTextLiteral(firstResultValue)},
  'other_workspace_id', ${otherWorkspaceId ? `${sqlUuid(otherWorkspaceId)}::text` : "NULL"},
  'other_dataset_id', ${otherDatasetId ? `${sqlUuid(otherDatasetId)}::text` : "NULL"},
  'other_experiment_id', ${otherExperimentId ? `${sqlUuid(otherExperimentId)}::text` : "NULL"},
  'other_experiment_dataset_id', ${otherExperimentDatasetId ? `${sqlUuid(otherExperimentDatasetId)}::text` : "NULL"},
  'other_blocked_dataset_name', ${sqlTextLiteral(otherBlockedDatasetName)}
);
`;
  return runPostgresJson(sql);
}

async function loadExperimentDatasetMaterializeAudit(fixture) {
  assert(
    isUuid(fixture.organization_id),
    "fixture organization id required for DB audit.",
  );
  const sql = `
SELECT json_build_object(
  'duplicate_name_active_count', (
    SELECT count(*)
    FROM model_hub_dataset d
    WHERE d.name = ${sqlTextLiteral(fixture.duplicate_dataset_name)}
      AND d.organization_id = ${sqlUuid(fixture.organization_id)}
      AND d.deleted = false
  ),
  'blocked_dataset_count', (
    SELECT count(*)
    FROM model_hub_dataset d
    WHERE d.name = ${sqlTextLiteral(fixture.other_blocked_dataset_name)}
      AND d.organization_id = ${sqlUuid(fixture.organization_id)}
      AND d.deleted = false
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteExperimentDatasetMaterializeFixture(fixture) {
  const datasetIds = [
    fixture.dataset_id,
    fixture.duplicate_dataset_id,
    fixture.other_dataset_id,
  ].filter(isUuid);
  const experimentIds = [
    fixture.experiment_id,
    fixture.other_experiment_id,
  ].filter(isUuid);
  const experimentDatasetIds = [
    fixture.experiment_dataset_id,
    fixture.other_experiment_dataset_id,
  ].filter(isUuid);
  assert(
    datasetIds.length > 0,
    "Experiment dataset materialize cleanup missing dataset ids.",
  );
  assert(
    isUuid(fixture.organization_id),
    "Experiment dataset materialize cleanup missing organization id.",
  );
  const sql = `
WITH target_datasets AS (
  SELECT id
  FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
     OR (
       organization_id = ${sqlUuid(fixture.organization_id)}
       AND name = ${sqlTextLiteral(fixture.other_blocked_dataset_name)}
     )
),
deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_experiment_m2m AS (
  DELETE FROM model_hub_experimentstable_experiments_datasets
  WHERE experimentstable_id = ANY(${sqlUuidArray(experimentIds)})
     OR experimentdatasettable_id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiment_dataset_columns AS (
  DELETE FROM model_hub_experimentdatasettable_columns
  WHERE experimentdatasettable_id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiment_datasets AS (
  DELETE FROM model_hub_experimentdatasettable
  WHERE id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiments AS (
  DELETE FROM model_hub_experimentstable
  WHERE id = ANY(${sqlUuidArray(experimentIds)})
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id IN (SELECT id FROM target_datasets)
  RETURNING 1
)
SELECT json_build_object(
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_experiment_m2m_count', (SELECT count(*) FROM deleted_experiment_m2m),
  'deleted_experiment_dataset_column_count', (SELECT count(*) FROM deleted_experiment_dataset_columns),
  'deleted_experiment_dataset_count', (SELECT count(*) FROM deleted_experiment_datasets),
  'deleted_experiment_count', (SELECT count(*) FROM deleted_experiments),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets)
);
`;
  return runPostgresJson(sql);
}

async function seedHuggingFaceGuardFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const activeWorkspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const otherWorkspaceId = await findOtherWorkspaceId(
    organizationId,
    workspaceId,
  );
  const duplicateDatasetId = randomUUID();
  const otherDatasetId = otherWorkspaceId ? randomUUID() : null;
  const otherColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherRowId = otherWorkspaceId ? randomUUID() : null;
  const otherCellId = otherWorkspaceId ? randomUUID() : null;
  const duplicateDatasetName = `api journey hf duplicate ${runId}`;
  const otherDatasetName = `api journey hf other ${runId}`;
  const otherDatasetSql = otherWorkspaceId
    ? `,
  (
    ${sqlUuid(otherDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(otherDatasetName)},
    ARRAY[${sqlTextLiteral(otherColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${sqlUuid(otherWorkspaceId)},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )`
    : "";
  const otherChildSql = otherWorkspaceId
    ? `,
inserted_other_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(otherColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'existing',
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(otherDatasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_other_row AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES (
    ${sqlUuid(otherRowId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    5,
    ${sqlUuid(otherDatasetId)},
    '{}'::jsonb
  )
  RETURNING id
),
inserted_other_cell AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES (
    ${sqlUuid(otherCellId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'existing other workspace row',
    ${sqlUuid(otherColumnId)},
    ${sqlUuid(otherDatasetId)},
    ${sqlUuid(otherRowId)},
    'pass',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )
  RETURNING id
)`
    : "";
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
  (
    ${sqlUuid(duplicateDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(duplicateDatasetName)},
    ARRAY[]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${activeWorkspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )${otherDatasetSql}
  RETURNING id
)${otherChildSql}
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_datasets) >= 1,
  'duplicate_dataset_id', ${sqlUuid(duplicateDatasetId)}::text,
  'duplicate_dataset_name', ${sqlTextLiteral(duplicateDatasetName)},
  'other_workspace_id', ${otherWorkspaceId ? `${sqlUuid(otherWorkspaceId)}::text` : "NULL"},
  'other_dataset_id', ${otherDatasetId ? `${sqlUuid(otherDatasetId)}::text` : "NULL"},
  'other_initial_row_count', ${otherWorkspaceId ? 1 : 0},
  'other_initial_column_count', ${otherWorkspaceId ? 1 : 0}
);
`;
  return runPostgresJson(sql);
}

async function loadHuggingFaceGuardFixtureAudit(
  fixture,
  organizationId,
  workspaceId,
) {
  assert(
    isUuid(fixture.duplicate_dataset_id),
    "fixture duplicate dataset id required.",
  );
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const otherDatasetSelect = fixture.other_dataset_id
    ? `(SELECT count(*) FROM model_hub_row r WHERE r.dataset_id = ${sqlUuid(fixture.other_dataset_id)} AND r.deleted = false)`
    : "0";
  const otherColumnSelect = fixture.other_dataset_id
    ? `(SELECT count(*) FROM model_hub_column c WHERE c.dataset_id = ${sqlUuid(fixture.other_dataset_id)} AND c.deleted = false)`
    : "0";
  const sql = `
SELECT json_build_object(
  'duplicate_name_active_count', (
    SELECT count(*)
    FROM model_hub_dataset d
    WHERE d.name = ${sqlTextLiteral(fixture.duplicate_dataset_name)}
      AND d.organization_id = ${sqlUuid(organizationId)}
      AND d.deleted = false
  ),
  'duplicate_dataset_workspace_id', (
    SELECT d.workspace_id::text
    FROM model_hub_dataset d
    WHERE d.id = ${sqlUuid(fixture.duplicate_dataset_id)}
  ),
  'other_dataset_row_count', ${otherDatasetSelect},
  'other_dataset_column_count', ${otherColumnSelect}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteHuggingFaceGuardFixture(fixture) {
  const datasetIds = [
    fixture.duplicate_dataset_id,
    fixture.other_dataset_id,
  ].filter(isUuid);
  assert(
    datasetIds.length > 0,
    "Hugging Face fixture cleanup missing datasets.",
  );
  const sql = `
WITH deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
)
SELECT json_build_object(
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets)
);
`;
  return runPostgresJson(sql);
}

async function seedLegacyExperimentRowDiffFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const outsideDatasetId = randomUUID();
  const experimentId = randomUUID();
  const experimentDatasetId = randomUUID();
  const baseColumnId = randomUUID();
  const otherColumnId = randomUUID();
  const outsideColumnId = randomUUID();
  const rowId = randomUUID();
  const outsideRowId = randomUUID();
  const baseCellValue = `legacy base answer ${suffix}`;
  const otherCellValue = `legacy other response ${suffix}`;
  const outsideCellValue = `outside legacy row diff ${suffix}`;
  const datasetName = `api journey legacy diff ${runId}`;
  const outsideDatasetName = `api journey legacy diff outside ${runId}`;
  const columnValues = [
    [baseColumnId, "legacy_prompt_a", datasetId],
    [otherColumnId, "legacy_prompt_b", datasetId],
    [outsideColumnId, "legacy_outside", outsideDatasetId],
  ]
    .map(
      ([id, name, columnDatasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    'text',
    'experiment',
    NULL::varchar,
    ${sqlUuid(columnDatasetId)},
    '{}'::jsonb,
    'Completed'
  )`,
    )
    .join(",\n");
  const cellValues = [
    [
      randomUUID(),
      baseCellValue,
      baseColumnId,
      datasetId,
      rowId,
      { metadata: { prompt: "base" } },
    ],
    [
      randomUUID(),
      otherCellValue,
      otherColumnId,
      datasetId,
      rowId,
      { metadata: { prompt: "other" } },
    ],
    [
      randomUUID(),
      outsideCellValue,
      outsideColumnId,
      outsideDatasetId,
      outsideRowId,
      { metadata: { prompt: "outside" } },
    ],
  ]
    .map(
      ([id, value, columnId, cellDatasetId, cellRowId, valueInfos]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(value)},
    ${sqlUuid(columnId)},
    ${sqlUuid(cellDatasetId)},
    ${sqlUuid(cellRowId)},
    'pass',
    ${sqlJsonLiteral(valueInfos)},
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
  (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(baseColumnId)}, ${sqlTextLiteral(otherColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  ),
  (
    ${sqlUuid(outsideDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(outsideDatasetName)},
    ARRAY[${sqlTextLiteral(outsideColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
${columnValues}
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
  (
    ${sqlUuid(rowId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    0,
    ${sqlUuid(datasetId)},
    '{}'::jsonb
  ),
  (
    ${sqlUuid(outsideRowId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    0,
    ${sqlUuid(outsideDatasetId)},
    '{}'::jsonb
  )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellValues}
  RETURNING id
),
inserted_experiment AS (
  INSERT INTO model_hub_experimentstable (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    prompt_config,
    status,
    dataset_id,
    column_id,
    user_id,
    experiment_type,
    snapshot_dataset_id
  )
  VALUES (
    ${sqlUuid(experimentId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(`api journey legacy diff experiment ${runId}`)},
    '[]'::jsonb,
    'Completed',
    ${sqlUuid(datasetId)},
    ${sqlUuid(baseColumnId)},
    NULL::uuid,
    'llm',
    NULL::uuid
  )
  RETURNING id
),
inserted_experiment_dataset AS (
  INSERT INTO model_hub_experimentdatasettable (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    prompt_config,
    exclude_values,
    status,
    experiment_id
  )
  VALUES (
    ${sqlUuid(experimentDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'legacy_prompt_a',
    '{}'::jsonb,
    '{}'::jsonb,
    'Completed',
    ${sqlUuid(experimentId)}
  )
  RETURNING id
),
inserted_experiment_dataset_columns AS (
  INSERT INTO model_hub_experimentdatasettable_columns (
    experimentdatasettable_id,
    column_id
  )
  VALUES
  (${sqlUuid(experimentDatasetId)}, ${sqlUuid(baseColumnId)}),
  (${sqlUuid(experimentDatasetId)}, ${sqlUuid(otherColumnId)})
  RETURNING id
),
inserted_experiment_dataset_m2m AS (
  INSERT INTO model_hub_experimentstable_experiments_datasets (
    experimentstable_id,
    experimentdatasettable_id
  )
  VALUES (${sqlUuid(experimentId)}, ${sqlUuid(experimentDatasetId)})
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_experiment) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'outside_dataset_id', ${sqlUuid(outsideDatasetId)}::text,
  'experiment_id', ${sqlUuid(experimentId)}::text,
  'experiment_dataset_id', ${sqlUuid(experimentDatasetId)}::text,
  'base_column_id', ${sqlUuid(baseColumnId)}::text,
  'other_column_id', ${sqlUuid(otherColumnId)}::text,
  'outside_column_id', ${sqlUuid(outsideColumnId)}::text,
  'row_id', ${sqlUuid(rowId)}::text,
  'outside_row_id', ${sqlUuid(outsideRowId)}::text,
  'base_cell_value', ${sqlTextLiteral(baseCellValue)},
  'other_cell_value', ${sqlTextLiteral(otherCellValue)},
  'outside_cell_value', ${sqlTextLiteral(outsideCellValue)}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteLegacyExperimentRowDiffFixture(fixture) {
  const datasetIds = [fixture.dataset_id, fixture.outside_dataset_id].filter(
    isUuid,
  );
  const experimentIds = [fixture.experiment_id].filter(isUuid);
  const experimentDatasetIds = [fixture.experiment_dataset_id].filter(isUuid);
  assert(datasetIds.length > 0, "Legacy row-diff cleanup missing dataset ids.");
  assert(
    experimentIds.length > 0,
    "Legacy row-diff cleanup missing experiment ids.",
  );
  assert(
    experimentDatasetIds.length > 0,
    "Legacy row-diff cleanup missing experiment dataset ids.",
  );
  const sql = `
WITH deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_experiment_m2m AS (
  DELETE FROM model_hub_experimentstable_experiments_datasets
  WHERE experimentstable_id = ANY(${sqlUuidArray(experimentIds)})
     OR experimentdatasettable_id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiment_dataset_columns AS (
  DELETE FROM model_hub_experimentdatasettable_columns
  WHERE experimentdatasettable_id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiment_datasets AS (
  DELETE FROM model_hub_experimentdatasettable
  WHERE id = ANY(${sqlUuidArray(experimentDatasetIds)})
  RETURNING 1
),
deleted_experiments AS (
  DELETE FROM model_hub_experimentstable
  WHERE id = ANY(${sqlUuidArray(experimentIds)})
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
)
SELECT json_build_object(
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_experiment_m2m_count', (SELECT count(*) FROM deleted_experiment_m2m),
  'deleted_experiment_dataset_column_count', (SELECT count(*) FROM deleted_experiment_dataset_columns),
  'deleted_experiment_dataset_count', (SELECT count(*) FROM deleted_experiment_datasets),
  'deleted_experiment_count', (SELECT count(*) FROM deleted_experiments),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets)
);
`;
  return runPostgresJson(sql);
}

async function seedLegacyExperimentCreateUpdateFixture({
  runId,
  organizationId,
  workspaceId,
  userId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const activeWorkspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const userSql = userId && isUuid(userId) ? sqlUuid(userId) : "NULL::uuid";
  const otherWorkspaceId = await findOtherWorkspaceId(
    organizationId,
    workspaceId,
  );
  const datasetId = randomUUID();
  const outputColumnId = randomUUID();
  const blockedDatasetId = randomUUID();
  const blockedColumnId = randomUUID();
  const evalTemplateId = randomUUID();
  const evalMetricId = randomUUID();
  const secondEvalMetricId = randomUUID();
  const blockedEvalMetricId = randomUUID();
  const otherDatasetId = otherWorkspaceId ? randomUUID() : null;
  const otherColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherExperimentId = otherWorkspaceId ? randomUUID() : null;
  const datasetName = `api journey legacy exp dataset ${runId}`;
  const blockedDatasetName = `api journey legacy blocked dataset ${runId}`;
  const evalTemplateName = `api journey legacy exp eval ${runId}`;
  const otherExperimentName = `api journey legacy other ws ${runId}`;
  const blockedColumnExperimentName = `api journey legacy blocked column ${runId}`;
  const blockedMetricExperimentName = `api journey legacy blocked metric ${runId}`;
  const otherWorkspaceCreateName = `api journey legacy other create ${runId}`;
  const otherDatasetSql = otherWorkspaceId
    ? `,
  (
    ${sqlUuid(otherDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(`api journey legacy other dataset ${runId}`)},
    ARRAY[${sqlTextLiteral(otherColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${sqlUuid(otherWorkspaceId)},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )`
    : "";
  const otherColumnSql = otherWorkspaceId
    ? `,
  (
    ${sqlUuid(otherColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Other Workspace Output',
    'text',
    'run_prompt',
    '',
    ${sqlUuid(otherDatasetId)},
    '{}'::jsonb,
    'NotStarted'
  )`
    : "";
  const otherExperimentSql = otherWorkspaceId
    ? `,
inserted_other_experiment AS (
  INSERT INTO model_hub_experimentstable (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    prompt_config,
    status,
    dataset_id,
    column_id,
    user_id,
    experiment_type,
    snapshot_dataset_id
  )
  VALUES (
    ${sqlUuid(otherExperimentId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(otherExperimentName)},
    ${sqlJsonLiteral({ model: "gpt-4o-mini", journey: "EXP-API-004-other" })},
    'NotStarted',
    ${sqlUuid(otherDatasetId)},
    ${sqlUuid(otherColumnId)},
    ${userSql},
    'llm',
    NULL::uuid
  )
  RETURNING id
)`
    : "";
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
  (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(outputColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${activeWorkspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  ),
  (
    ${sqlUuid(blockedDatasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(blockedDatasetName)},
    ARRAY[${sqlTextLiteral(blockedColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${activeWorkspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )${otherDatasetSql}
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
  (
    ${sqlUuid(outputColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Legacy Output',
    'text',
    'run_prompt',
    '',
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'NotStarted'
  ),
  (
    ${sqlUuid(blockedColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Blocked Other Dataset Column',
    'text',
    'run_prompt',
    '',
    ${sqlUuid(blockedDatasetId)},
    '{}'::jsonb,
    'NotStarted'
  )${otherColumnSql}
  RETURNING id
),
inserted_eval_template AS (
  INSERT INTO model_hub_evaltemplate (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    owner,
    eval_tags,
    config,
    organization_id,
    description,
    eval_id,
    criteria,
    choices,
    multi_choice,
    model,
    proxy_agi,
    visible_ui,
    evaluator_id,
    workspace_id,
    choice_scores,
    output_type_normalized,
    pass_threshold,
    template_type,
    eval_type,
    allow_edit,
    allow_copy,
    error_localizer_enabled,
    aggregation_enabled,
    aggregation_function,
    composite_child_axis
  )
  VALUES (
    ${sqlUuid(evalTemplateId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(evalTemplateName)},
    'user',
    ARRAY[]::varchar[],
    ${sqlJsonLiteral({ required_keys: ["output"] })},
    ${sqlUuid(organizationId)},
    'API journey legacy experiment eval',
    0,
    'Evaluate {{output}}',
    NULL::jsonb,
    false,
    'gpt-4o-mini',
    false,
    false,
    NULL::uuid,
    ${activeWorkspaceSql},
    NULL::jsonb,
    NULL::varchar,
    NULL::double precision,
    'single',
    'llm',
    true,
    true,
    false,
    false,
    'mean',
    'shared'
  )
  RETURNING id
),
inserted_eval_metrics AS (
  INSERT INTO model_hub_userevalmetric (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    config,
    dataset_id,
    template_id,
    organization_id,
    status,
    show_in_sidebar,
    source_id,
    column_deleted,
    user_id,
    error_localizer,
    kb_id,
    model,
    workspace_id,
    eval_group_id,
    composite_weight_overrides
  )
  VALUES
  (
    ${sqlUuid(evalMetricId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Legacy Create Metric',
    ${sqlJsonLiteral({ mapping: { output: outputColumnId }, config: {} })},
    ${sqlUuid(datasetId)},
    ${sqlUuid(evalTemplateId)},
    ${sqlUuid(organizationId)},
    'NotStarted',
    true,
    '',
    false,
    ${userSql},
    false,
    NULL::uuid,
    'gpt-4o-mini',
    ${activeWorkspaceSql},
    NULL::uuid,
    NULL::jsonb
  ),
  (
    ${sqlUuid(secondEvalMetricId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Legacy Update Metric',
    ${sqlJsonLiteral({ mapping: { output: outputColumnId }, config: {} })},
    ${sqlUuid(datasetId)},
    ${sqlUuid(evalTemplateId)},
    ${sqlUuid(organizationId)},
    'NotStarted',
    true,
    '',
    false,
    ${userSql},
    false,
    NULL::uuid,
    'gpt-4o-mini',
    ${activeWorkspaceSql},
    NULL::uuid,
    NULL::jsonb
  ),
  (
    ${sqlUuid(blockedEvalMetricId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'Legacy Blocked Metric',
    ${sqlJsonLiteral({ mapping: { output: blockedColumnId }, config: {} })},
    ${sqlUuid(blockedDatasetId)},
    ${sqlUuid(evalTemplateId)},
    ${sqlUuid(organizationId)},
    'NotStarted',
    true,
    '',
    false,
    ${userSql},
    false,
    NULL::uuid,
    'gpt-4o-mini',
    ${activeWorkspaceSql},
    NULL::uuid,
    NULL::jsonb
  )
  RETURNING id
)${otherExperimentSql}
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_datasets) >= 2,
  'organization_id', ${sqlUuid(organizationId)}::text,
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL"},
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'output_column_id', ${sqlUuid(outputColumnId)}::text,
  'blocked_dataset_id', ${sqlUuid(blockedDatasetId)}::text,
  'blocked_column_id', ${sqlUuid(blockedColumnId)}::text,
  'eval_template_id', ${sqlUuid(evalTemplateId)}::text,
  'eval_metric_id', ${sqlUuid(evalMetricId)}::text,
  'second_eval_metric_id', ${sqlUuid(secondEvalMetricId)}::text,
  'blocked_eval_metric_id', ${sqlUuid(blockedEvalMetricId)}::text,
  'blocked_column_experiment_name', ${sqlTextLiteral(blockedColumnExperimentName)},
  'blocked_metric_experiment_name', ${sqlTextLiteral(blockedMetricExperimentName)},
  'other_workspace_id', ${otherWorkspaceId ? `${sqlUuid(otherWorkspaceId)}::text` : "NULL"},
  'other_dataset_id', ${otherDatasetId ? `${sqlUuid(otherDatasetId)}::text` : "NULL"},
  'other_column_id', ${otherColumnId ? `${sqlUuid(otherColumnId)}::text` : "NULL"},
  'other_experiment_id', ${otherExperimentId ? `${sqlUuid(otherExperimentId)}::text` : "NULL"},
  'other_experiment_name', ${sqlTextLiteral(otherExperimentName)},
  'other_workspace_create_name', ${sqlTextLiteral(otherWorkspaceCreateName)}
);
`;
  return runPostgresJson(sql);
}

async function loadLegacyExperimentCreateUpdateAudit({
  experimentId,
  datasetId,
  experimentName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (experimentId)
    assert(isUuid(experimentId), "experimentId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const experimentPredicate = experimentId
    ? `e.id = ${sqlUuid(experimentId)}`
    : `e.name = ${sqlTextLiteral(experimentName)}`;
  const workspacePredicate = workspaceId
    ? `AND d.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH experiment_row AS (
  SELECT
    e.id,
    e.name,
    e.prompt_config,
    e.status,
    e.dataset_id,
    e.column_id,
    e.user_id,
    e.deleted,
    d.organization_id,
    d.workspace_id,
    c.dataset_id AS column_dataset_id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  LEFT JOIN model_hub_column c ON c.id = e.column_id
  WHERE ${experimentPredicate}
    AND e.dataset_id = ${sqlUuid(datasetId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
    ${workspacePredicate}
    AND e.deleted = false
  ORDER BY e.created_at DESC
  LIMIT 1
)
SELECT COALESCE((
  SELECT json_build_object(
    'experiment_id', e.id::text,
    'experiment_name', e.name,
    'prompt_config', e.prompt_config,
    'status', e.status,
    'dataset_id', e.dataset_id::text,
    'column_id', e.column_id::text,
    'organization_id', e.organization_id::text,
    'workspace_id', e.workspace_id::text,
    'column_dataset_id', e.column_dataset_id::text,
    'metric_count', (
      SELECT count(*)
      FROM model_hub_experimentstable_user_eval_template_ids m
      WHERE m.experimentstable_id = e.id
    ),
    'metric_ids', (
      SELECT COALESCE(json_agg(m.userevalmetric_id::text ORDER BY m.userevalmetric_id::text), '[]'::json)
      FROM model_hub_experimentstable_user_eval_template_ids m
      WHERE m.experimentstable_id = e.id
    )
  )
  FROM experiment_row e
), '{}'::json);
`;
  return runPostgresJson(sql);
}

function assertLegacyExperimentAudit(
  audit,
  {
    experimentName,
    datasetId,
    columnId,
    organizationId,
    workspaceId,
    metricIds,
    promptConfigJourney,
  },
) {
  assert(isUuid(audit?.experiment_id), "Legacy experiment audit found no row.");
  assert(
    audit.experiment_name === experimentName,
    "Legacy experiment audit name mismatch.",
  );
  assert(
    audit.dataset_id === datasetId,
    "Legacy experiment audit dataset mismatch.",
  );
  assert(
    audit.column_id === columnId,
    "Legacy experiment audit column mismatch.",
  );
  assert(
    audit.column_dataset_id === datasetId,
    "Legacy experiment audit column was not in the experiment dataset.",
  );
  assert(
    audit.organization_id === organizationId,
    "Legacy experiment audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Legacy experiment audit workspace mismatch.",
    );
  }
  assert(
    audit.prompt_config?.journey === promptConfigJourney,
    "Legacy experiment audit prompt_config mismatch.",
  );
  const actualMetricIds = payloadArray(audit.metric_ids, "metric_ids").sort();
  assert(
    JSON.stringify(actualMetricIds) === JSON.stringify([...metricIds].sort()),
    "Legacy experiment audit metric relation mismatch.",
  );
}

async function loadLegacyExperimentGuardAudit(fixture) {
  assert(isUuid(fixture.dataset_id), "fixture dataset id is required.");
  const otherExperimentNameSelect = fixture.other_experiment_id
    ? `(SELECT name FROM model_hub_experimentstable WHERE id = ${sqlUuid(fixture.other_experiment_id)})`
    : "NULL";
  const sql = `
SELECT json_build_object(
  'blocked_column_count', (
    SELECT count(*)
    FROM model_hub_experimentstable e
    WHERE e.name = ${sqlTextLiteral(fixture.blocked_column_experiment_name)}
      AND e.deleted = false
  ),
  'blocked_metric_count', (
    SELECT count(*)
    FROM model_hub_experimentstable e
    WHERE e.name = ${sqlTextLiteral(fixture.blocked_metric_experiment_name)}
      AND e.deleted = false
  ),
  'other_workspace_create_count', (
    SELECT count(*)
    FROM model_hub_experimentstable e
    WHERE e.name = ${sqlTextLiteral(fixture.other_workspace_create_name)}
      AND e.deleted = false
  ),
  'other_experiment_name', ${otherExperimentNameSelect}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteLegacyExperimentCreateUpdateFixture(fixture) {
  const datasetIds = [
    fixture.dataset_id,
    fixture.blocked_dataset_id,
    fixture.other_dataset_id,
  ].filter(isUuid);
  const experimentIds = [
    fixture.created_experiment_id,
    fixture.other_experiment_id,
  ].filter(isUuid);
  const metricIds = [
    fixture.eval_metric_id,
    fixture.second_eval_metric_id,
    fixture.blocked_eval_metric_id,
  ].filter(isUuid);
  const templateIds = [fixture.eval_template_id].filter(isUuid);
  assert(datasetIds.length > 0, "Legacy experiment cleanup missing datasets.");
  const experimentIdArray = experimentIds.length
    ? sqlUuidArray(experimentIds)
    : "ARRAY[]::uuid[]";
  const sql = `
WITH target_datasets AS (
  SELECT id
  FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
),
target_experiments AS (
  SELECT id
  FROM model_hub_experimentstable
  WHERE id = ANY(${experimentIdArray})
     OR dataset_id IN (SELECT id FROM target_datasets)
     OR name IN (
       ${sqlTextLiteral(fixture.blocked_column_experiment_name)},
       ${sqlTextLiteral(fixture.blocked_metric_experiment_name)},
       ${sqlTextLiteral(fixture.other_workspace_create_name)}
     )
),
deleted_pending_tasks AS (
  DELETE FROM model_hub_pendingrowtask
  WHERE experiment_id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_metric_m2m AS (
  DELETE FROM model_hub_experimentstable_user_eval_template_ids
  WHERE experimentstable_id IN (SELECT id FROM target_experiments)
     OR userevalmetric_id = ANY(${sqlUuidArray(metricIds)})
  RETURNING 1
),
deleted_experiment_m2m AS (
  DELETE FROM model_hub_experimentstable_experiments_datasets
  WHERE experimentstable_id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_experiment_dataset_columns AS (
  DELETE FROM model_hub_experimentdatasettable_columns edtc
  USING model_hub_experimentdatasettable edt
  WHERE edtc.experimentdatasettable_id = edt.id
    AND edt.experiment_id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_experiment_datasets AS (
  DELETE FROM model_hub_experimentdatasettable
  WHERE experiment_id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_comparisons AS (
  DELETE FROM model_hub_experimentcomparison
  WHERE experiment_id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_experiments AS (
  DELETE FROM model_hub_experimentstable
  WHERE id IN (SELECT id FROM target_experiments)
  RETURNING 1
),
deleted_metrics AS (
  DELETE FROM model_hub_userevalmetric
  WHERE id = ANY(${sqlUuidArray(metricIds)})
  RETURNING 1
),
deleted_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id = ANY(${sqlUuidArray(templateIds)})
  RETURNING 1
),
deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id IN (SELECT id FROM target_datasets)
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id IN (SELECT id FROM target_datasets)
  RETURNING 1
)
SELECT json_build_object(
  'deleted_pending_task_count', (SELECT count(*) FROM deleted_pending_tasks),
  'deleted_metric_m2m_count', (SELECT count(*) FROM deleted_metric_m2m),
  'deleted_experiment_m2m_count', (SELECT count(*) FROM deleted_experiment_m2m),
  'deleted_experiment_dataset_column_count', (SELECT count(*) FROM deleted_experiment_dataset_columns),
  'deleted_experiment_dataset_count', (SELECT count(*) FROM deleted_experiment_datasets),
  'deleted_comparison_count', (SELECT count(*) FROM deleted_comparisons),
  'deleted_experiment_count', (SELECT count(*) FROM deleted_experiments),
  'deleted_metric_count', (SELECT count(*) FROM deleted_metrics),
  'deleted_template_count', (SELECT count(*) FROM deleted_templates),
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets)
);
`;
  return runPostgresJson(sql);
}

async function seedDatasetCopyFixture({ runId, organizationId, workspaceId }) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const sourceDatasetId = randomUUID();
  const targetDatasetId = randomUUID();
  const sourceInputColumnId = randomUUID();
  const sourceOutputColumnId = randomUUID();
  const targetInputColumnId = randomUUID();
  const targetOutputColumnId = randomUUID();
  const sourceRowOneId = randomUUID();
  const sourceRowTwoId = randomUUID();
  const targetHighRowId = randomUUID();
  const targetLowRowId = randomUUID();
  const sourceInputOneValue = `api copy source one ${suffix}`;
  const sourceInputTwoValue = `api copy source two ${suffix}`;
  const datasetRows = [
    {
      id: sourceDatasetId,
      name: `api journey copy source ${runId}`,
      columnIds: [sourceInputColumnId, sourceOutputColumnId],
    },
    {
      id: targetDatasetId,
      name: `api journey copy target ${runId}`,
      columnIds: [targetInputColumnId, targetOutputColumnId],
    },
  ];
  const columnRows = [
    [sourceInputColumnId, "input", "text", "OTHERS", null, sourceDatasetId],
    [sourceOutputColumnId, "output", "text", "OTHERS", null, sourceDatasetId],
    [targetInputColumnId, "input", "text", "OTHERS", null, targetDatasetId],
    [targetOutputColumnId, "output", "text", "OTHERS", null, targetDatasetId],
  ];
  const rowRows = [
    [sourceRowOneId, 0, sourceDatasetId, "now() - interval '3 minutes'"],
    [sourceRowTwoId, 1, sourceDatasetId, "now() - interval '2 minutes'"],
    [targetHighRowId, 10, targetDatasetId, "now() - interval '10 minutes'"],
    [targetLowRowId, 0, targetDatasetId, "now()"],
  ];
  const cellRows = [
    [
      randomUUID(),
      sourceInputOneValue,
      sourceInputColumnId,
      sourceDatasetId,
      sourceRowOneId,
    ],
    [
      randomUUID(),
      `source output one ${suffix}`,
      sourceOutputColumnId,
      sourceDatasetId,
      sourceRowOneId,
    ],
    [
      randomUUID(),
      sourceInputTwoValue,
      sourceInputColumnId,
      sourceDatasetId,
      sourceRowTwoId,
    ],
    [
      randomUUID(),
      `source output two ${suffix}`,
      sourceOutputColumnId,
      sourceDatasetId,
      sourceRowTwoId,
    ],
    [
      randomUUID(),
      `target high input ${suffix}`,
      targetInputColumnId,
      targetDatasetId,
      targetHighRowId,
    ],
    [
      randomUUID(),
      `target high output ${suffix}`,
      targetOutputColumnId,
      targetDatasetId,
      targetHighRowId,
    ],
    [
      randomUUID(),
      `target low input ${suffix}`,
      targetInputColumnId,
      targetDatasetId,
      targetLowRowId,
    ],
    [
      randomUUID(),
      `target low output ${suffix}`,
      targetOutputColumnId,
      targetDatasetId,
      targetLowRowId,
    ],
  ];
  const datasetValues = datasetRows
    .map(
      (row) => `(
    ${sqlUuid(row.id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(row.name)},
    ARRAY[${row.columnIds.map((id) => sqlTextLiteral(id)).join(", ")}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )`,
    )
    .join(",\n");
  const columnValues = columnRows
    .map(
      ([id, name, dataType, source, sourceId, datasetId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(name)},
    ${sqlTextLiteral(dataType)},
    ${sqlTextLiteral(source)},
    ${sourceId ? sqlTextLiteral(sourceId) : "NULL::varchar"},
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )`,
    )
    .join(",\n");
  const rowValues = rowRows
    .map(
      ([id, order, datasetId, createdAtSql]) => `(
    ${sqlUuid(id)},
    ${createdAtSql},
    ${createdAtSql},
    false,
    NULL::timestamptz,
    ${Number(order)},
    ${sqlUuid(datasetId)},
    '{}'::jsonb
  )`,
    )
    .join(",\n");
  const cellValues = cellRows
    .map(
      ([id, value, columnId, datasetId, rowId]) => `(
    ${sqlUuid(id)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(value)},
    ${sqlUuid(columnId)},
    ${sqlUuid(datasetId)},
    ${sqlUuid(rowId)},
    'pass',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
${datasetValues}
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
${columnValues}
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_datasets) = 2,
  'source_dataset_id', ${sqlUuid(sourceDatasetId)}::text,
  'target_dataset_id', ${sqlUuid(targetDatasetId)}::text,
  'source_input_column_id', ${sqlUuid(sourceInputColumnId)}::text,
  'source_output_column_id', ${sqlUuid(sourceOutputColumnId)}::text,
  'target_input_column_id', ${sqlUuid(targetInputColumnId)}::text,
  'target_output_column_id', ${sqlUuid(targetOutputColumnId)}::text,
  'source_row_one_id', ${sqlUuid(sourceRowOneId)}::text,
  'source_row_two_id', ${sqlUuid(sourceRowTwoId)}::text,
  'target_high_row_id', ${sqlUuid(targetHighRowId)}::text,
  'target_low_row_id', ${sqlUuid(targetLowRowId)}::text,
  'source_input_one_value', ${sqlTextLiteral(sourceInputOneValue)},
  'source_input_two_value', ${sqlTextLiteral(sourceInputTwoValue)},
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function loadDatasetCopyFixtureAudit(fixture) {
  const requiredIds = [
    fixture.source_dataset_id,
    fixture.target_dataset_id,
    fixture.source_input_column_id,
    fixture.target_input_column_id,
  ];
  assert(
    requiredIds.every(isUuid),
    "Dataset copy audit fixture ids must be UUIDs.",
  );
  const sql = `
WITH source_rows AS (
  SELECT r.id, r."order"
  FROM model_hub_row r
  WHERE r.dataset_id = ${sqlUuid(fixture.source_dataset_id)}
    AND r.deleted = false
),
target_rows AS (
  SELECT r.id, r."order"
  FROM model_hub_row r
  WHERE r.dataset_id = ${sqlUuid(fixture.target_dataset_id)}
    AND r.deleted = false
)
SELECT json_build_object(
  'source_input_one_value', ${sqlTextLiteral(fixture.source_input_one_value)},
  'source_input_two_value', ${sqlTextLiteral(fixture.source_input_two_value)},
  'source_row_count', (SELECT count(*) FROM source_rows),
  'source_max_order', COALESCE((SELECT max("order") FROM source_rows), -1),
  'source_duplicate_value_count', (
    SELECT count(DISTINCT r.id)
    FROM source_rows r
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ${sqlUuid(fixture.source_input_column_id)}
     AND c.deleted = false
    WHERE c.value = ${sqlTextLiteral(fixture.source_input_one_value)}
  ),
  'target_row_count', (SELECT count(*) FROM target_rows),
  'target_max_order', COALESCE((SELECT max("order") FROM target_rows), -1),
  'target_order_11_input', (
    SELECT c.value
    FROM target_rows r
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ${sqlUuid(fixture.target_input_column_id)}
     AND c.deleted = false
    WHERE r."order" = 11
    LIMIT 1
  ),
  'target_source_one_count', (
    SELECT count(DISTINCT r.id)
    FROM target_rows r
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ${sqlUuid(fixture.target_input_column_id)}
     AND c.deleted = false
    WHERE c.value = ${sqlTextLiteral(fixture.source_input_one_value)}
  ),
  'target_source_two_count', (
    SELECT count(DISTINCT r.id)
    FROM target_rows r
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ${sqlUuid(fixture.target_input_column_id)}
     AND c.deleted = false
    WHERE c.value = ${sqlTextLiteral(fixture.source_input_two_value)}
  ),
  'target_order_12_15_count', (
    SELECT count(*)
    FROM target_rows r
    WHERE r."order" BETWEEN 12 AND 15
  )
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteDatasetCopyFixture(datasetIds) {
  return hardDeleteDatasetCompareFixture(datasetIds);
}

async function seedDatasetFileImportFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const datasetName = `api journey file import ${runId}`;
  const inputColumnName = "input";
  const newColumnName = "file_extra";
  const originalFilename = `api-file-import-${suffix}.csv`;
  const existingInputOne = `existing file import one ${suffix}`;
  const existingInputTwo = `existing file import two ${suffix}`;
  const importedInputOne = `imported file one ${suffix}`;
  const importedInputTwo = `imported file two ${suffix}`;
  const importedExtraOne = `extra one ${suffix}`;
  const importedExtraTwo = `extra two ${suffix}`;
  const columnConfig = {
    [inputColumnId]: { is_visible: true, is_frozen: null },
  };
  const datasetConfig = {
    dataset_source_local: true,
    file_processing_status: "queued",
    file_processing_queued_at: new Date().toISOString(),
    original_filename: originalFilename,
    estimated_rows: 2,
    estimated_columns: 1,
  };
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(inputColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    ${sqlJsonLiteral(datasetConfig)},
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(inputColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(inputColumnName)},
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
    (
      ${sqlUuid(rowOneId)},
      now() - interval '2 minutes',
      now() - interval '2 minutes',
      false,
      NULL::timestamptz,
      4,
      ${sqlUuid(datasetId)},
      '{}'::jsonb
    ),
    (
      ${sqlUuid(rowTwoId)},
      now() - interval '1 minute',
      now() - interval '1 minute',
      false,
      NULL::timestamptz,
      5,
      ${sqlUuid(datasetId)},
      '{}'::jsonb
    )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
    (
      ${sqlUuid(randomUUID())},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(existingInputOne)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowOneId)},
      'pass',
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      NULL::integer,
      NULL::integer,
      NULL::double precision
    ),
    (
      ${sqlUuid(randomUUID())},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(existingInputTwo)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(datasetId)},
      ${sqlUuid(rowTwoId)},
      'pass',
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      NULL::integer,
      NULL::integer,
      NULL::double precision
    )
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'input_column_name', ${sqlTextLiteral(inputColumnName)},
  'new_column_name', ${sqlTextLiteral(newColumnName)},
  'original_filename', ${sqlTextLiteral(originalFilename)},
  'imported_input_one', ${sqlTextLiteral(importedInputOne)},
  'imported_input_two', ${sqlTextLiteral(importedInputTwo)},
  'imported_extra_one', ${sqlTextLiteral(importedExtraOne)},
  'imported_extra_two', ${sqlTextLiteral(importedExtraTwo)},
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function loadDatasetFileImportFixtureAudit(fixture) {
  assert(isUuid(fixture.dataset_id), "dataset_id must be a UUID for DB audit.");
  const sql = `
WITH rows AS (
  SELECT id, "order"
  FROM model_hub_row
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
columns AS (
  SELECT id, name
  FROM model_hub_column
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
new_column AS (
  SELECT id
  FROM columns
  WHERE name = ${sqlTextLiteral(fixture.new_column_name)}
  LIMIT 1
),
input_column AS (
  SELECT id
  FROM columns
  WHERE name = ${sqlTextLiteral(fixture.input_column_name)}
  LIMIT 1
)
SELECT json_build_object(
  'row_count', (SELECT count(*) FROM rows),
  'column_count', (SELECT count(*) FROM columns),
  'column_names', (
    SELECT COALESCE(json_agg(name ORDER BY name), '[]'::json)
    FROM columns
  ),
  'max_order', COALESCE((SELECT max("order") FROM rows), -1),
  'imported_input_values', (
    SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
    FROM rows r
    JOIN input_column ic ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = ic.id
     AND c.deleted = false
    WHERE r."order" >= 6
  ),
  'imported_extra_values', (
    SELECT COALESCE(json_agg(c.value ORDER BY r."order"), '[]'::json)
    FROM rows r
    JOIN new_column nc ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = nc.id
     AND c.deleted = false
    WHERE r."order" >= 6
  ),
  'existing_blank_cells_for_new_column', (
    SELECT count(*)
    FROM rows r
    JOIN new_column nc ON true
    JOIN model_hub_cell c
      ON c.row_id = r.id
     AND c.column_id = nc.id
     AND c.deleted = false
    WHERE r."order" < 6
      AND COALESCE(c.value, '') = ''
  ),
  'active_cell_count', (
    SELECT count(*)
    FROM model_hub_cell c
    WHERE c.dataset_id = ${sqlUuid(fixture.dataset_id)}
      AND c.deleted = false
  ),
  'progress_status', (
    SELECT dataset_config->>'file_processing_status'
    FROM model_hub_dataset
    WHERE id = ${sqlUuid(fixture.dataset_id)}
  )
);
`;
  return runPostgresJson(sql);
}

function assertDatasetFileImportFixtureAudit(audit, fixture) {
  assert(Number(audit.row_count) === 4, "File import row count mismatch.");
  assert(
    Number(audit.column_count) === 2,
    "File import column count mismatch.",
  );
  assert(
    asArray(audit.column_names).includes(fixture.new_column_name),
    "File import did not create the new CSV column.",
  );
  assert(
    Number(audit.max_order) === 7,
    "File import did not append after max order.",
  );
  assert(
    JSON.stringify(audit.imported_input_values) ===
      JSON.stringify([fixture.imported_input_one, fixture.imported_input_two]),
    "File import input values did not round-trip in order.",
  );
  assert(
    JSON.stringify(audit.imported_extra_values) ===
      JSON.stringify([fixture.imported_extra_one, fixture.imported_extra_two]),
    "File import new-column values did not round-trip in order.",
  );
  assert(
    Number(audit.existing_blank_cells_for_new_column) === 2,
    "File import did not backfill blank cells for existing rows.",
  );
  assert(
    Number(audit.active_cell_count) === 8,
    "File import cell count mismatch.",
  );
  assert(
    audit.progress_status === "queued",
    "File import changed the seeded local-file progress status unexpectedly.",
  );
}

async function seedDatasetDynamicColumnFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const payloadColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const datasetName = `api journey dynamic columns ${runId}`;
  const previewNameOne = `Dynamic Alice ${suffix}`;
  const previewNameTwo = `Dynamic Bob ${suffix}`;
  const columnConfig = {
    [payloadColumnId]: { is_visible: true, is_frozen: null },
  };
  const values = [
    [rowOneId, 1, `{"name":"${previewNameOne}","priority":"high"}`],
    [rowTwoId, 2, `{"name":"${previewNameTwo}","priority":"low"}`],
  ];
  const rowValues = values
    .map(
      ([rowId, order]) => `(
    ${sqlUuid(rowId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${Number(order)},
    ${sqlUuid(datasetId)},
    '{}'::jsonb
  )`,
    )
    .join(",\n");
  const cellValues = values
    .map(
      ([rowId, , value]) => `(
    ${sqlUuid(randomUUID())},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(value)},
    ${sqlUuid(payloadColumnId)},
    ${sqlUuid(datasetId)},
    ${sqlUuid(rowId)},
    'pass',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(payloadColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(payloadColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'payload',
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'payload_column_id', ${sqlUuid(payloadColumnId)}::text,
  'preview_name_one', ${sqlTextLiteral(previewNameOne)},
  'preview_name_two', ${sqlTextLiteral(previewNameTwo)},
  'extract_json_column_name', ${sqlTextLiteral(`json_name_${suffix}`)},
  'api_column_name', ${sqlTextLiteral(`api_result_${suffix}`)},
  'conditional_column_name', ${sqlTextLiteral(`conditional_${suffix}`)},
  'classify_column_name', ${sqlTextLiteral(`classification_${suffix}`)},
  'entities_column_name', ${sqlTextLiteral(`entities_${suffix}`)},
  'vector_column_name', ${sqlTextLiteral(`vector_${suffix}`)},
  'missing_guard_column_name', ${sqlTextLiteral(`missing_guard_${suffix}`)},
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function createDatasetDynamicColumns(client, fixture) {
  const extractJson = await client.post(
    apiPath("/model-hub/develops/{dataset_id}/extract-json-column/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      column_id: fixture.payload_column_id,
      json_key: "name",
      new_column_name: fixture.extract_json_column_name,
      concurrency: 1,
    },
  );
  const apiColumn = await client.post(
    apiPath("/model-hub/datasets/{dataset_id}/add-api-column/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      column_name: fixture.api_column_name,
      config: {
        url: "https://example.com",
        method: "GET",
        output_type: "string",
      },
      concurrency: 1,
    },
  );
  const conditional = await client.post(
    apiPath("/model-hub/datasets/{dataset_id}/conditional-column/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      new_column_name: fixture.conditional_column_name,
      config: [
        {
          branch_type: "else",
          condition: "",
          branch_node_config: {
            type: "static_value",
            config: { value: "fallback" },
          },
        },
      ],
      concurrency: 1,
    },
  );
  const classification = await client.post(
    apiPath("/model-hub/datasets/{dataset_id}/classify-column/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      column_id: fixture.payload_column_id,
      labels: ["alpha", "beta"],
      new_column_name: fixture.classify_column_name,
      language_model_id: "gpt-4o-mini",
      concurrency: 1,
    },
  );
  const entities = await client.post(
    apiPath("/model-hub/datasets/{dataset_id}/extract-entities/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      column_id: fixture.payload_column_id,
      instruction: "Extract names",
      new_column_name: fixture.entities_column_name,
      language_model_id: "gpt-4o-mini",
      concurrency: 1,
    },
  );
  const vector = await client.post(
    apiPath("/model-hub/datasets/{dataset_id}/add_vector_db_column/", {
      dataset_id: fixture.dataset_id,
    }),
    {
      column_id: fixture.payload_column_id,
      sub_type: "pinecone",
      api_key: randomUUID(),
      new_column_name: fixture.vector_column_name,
      index_name: "api-journey-index",
      top_k: 1,
      embedding_config: {
        type: "openai",
        model: "text-embedding-3-small",
      },
      concurrency: 1,
    },
  );

  const createdColumns = {
    [fixture.extract_json_column_name]: extractJson.new_column_id,
    [fixture.api_column_name]: apiColumn.new_column_id,
    [fixture.conditional_column_name]: conditional.new_column_id,
    [fixture.classify_column_name]: classification.new_column_id,
    [fixture.entities_column_name]: entities.new_column_id,
    [fixture.vector_column_name]: vector.new_column_id,
  };
  for (const [name, id] of Object.entries(createdColumns)) {
    assert(isUuid(id), `Dynamic-column API did not return a UUID for ${name}.`);
  }
  return createdColumns;
}

async function loadDatasetDynamicColumnFixtureAudit(fixture) {
  assert(isUuid(fixture.dataset_id), "dataset_id must be a UUID for DB audit.");
  const expectedNames = [
    fixture.extract_json_column_name,
    fixture.api_column_name,
    fixture.conditional_column_name,
    fixture.classify_column_name,
    fixture.entities_column_name,
    fixture.vector_column_name,
  ];
  const sql = `
WITH dataset_row AS (
  SELECT id, column_order
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
base_rows AS (
  SELECT id
  FROM model_hub_row
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
),
base_cells AS (
  SELECT id
  FROM model_hub_cell
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND column_id = ${sqlUuid(fixture.payload_column_id)}
    AND deleted = false
),
dynamic_columns AS (
  SELECT id, name, source, status, metadata
  FROM model_hub_column
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
    AND deleted = false
    AND name = ANY(${sqlTextArray(expectedNames)})
)
SELECT json_build_object(
  'row_count', (SELECT count(*) FROM base_rows),
  'base_cell_count', (SELECT count(*) FROM base_cells),
  'column_order_length', (
    SELECT COALESCE(cardinality(column_order), 0)
    FROM dataset_row
  ),
  'column_order', (
    SELECT COALESCE(array_to_json(column_order), '[]'::json)
    FROM dataset_row
  ),
  'dynamic_column_count', (SELECT count(*) FROM dynamic_columns),
  'dynamic_columns', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'name', name,
          'source', source,
          'status', status,
          'metadata', metadata
        )
        ORDER BY name
      ),
      '[]'::json
    )
    FROM dynamic_columns
  )
);
`;
  return runPostgresJson(sql);
}

function assertDatasetDynamicColumnFixtureAudit(
  audit,
  fixture,
  createdColumns,
) {
  assert(Number(audit.row_count) === 2, "Dynamic fixture row count mismatch.");
  assert(
    Number(audit.base_cell_count) === 2,
    "Dynamic fixture base cell count mismatch.",
  );
  assert(
    Number(audit.dynamic_column_count) === 6,
    "Dynamic fixture did not create all generated columns.",
  );
  assert(
    Number(audit.column_order_length) === 7,
    "Dynamic fixture column order did not include all generated columns.",
  );
  const columns = asArray(audit.dynamic_columns);
  const byName = new Map(columns.map((column) => [column.name, column]));
  const expected = [
    [fixture.extract_json_column_name, "extracted_json"],
    [fixture.api_column_name, "api_call"],
    [fixture.conditional_column_name, "conditional"],
    [fixture.classify_column_name, "classification"],
    [fixture.entities_column_name, "extracted_entities"],
    [fixture.vector_column_name, "vector_db"],
  ];
  for (const [name, source] of expected) {
    const column = byName.get(name);
    assert(column, `Dynamic column ${name} was missing from DB audit.`);
    assert(
      column.id === createdColumns[name],
      `Dynamic column ${name} id mismatch.`,
    );
    assert(column.source === source, `Dynamic column ${name} source mismatch.`);
    assert(
      ["Running", "Completed", "Error"].includes(column.status),
      `Dynamic column ${name} had an unexpected status.`,
    );
    assert(
      asArray(audit.column_order).includes(column.id),
      `Dynamic column ${name} was missing from dataset column_order.`,
    );
  }

  const extractJson = byName.get(fixture.extract_json_column_name);
  assert(
    extractJson.metadata?.column_id === fixture.payload_column_id &&
      extractJson.metadata?.json_key === "name",
    "Extract-json column metadata did not preserve source column and json_key.",
  );
  const apiColumn = byName.get(fixture.api_column_name);
  assert(
    apiColumn.metadata?.url === "https://example.com" &&
      apiColumn.metadata?.method === "GET",
    "API column metadata did not preserve request config.",
  );
  const conditional = byName.get(fixture.conditional_column_name);
  assert(
    asArray(conditional.metadata?.config).length === 1,
    "Conditional column metadata did not preserve branch config.",
  );
  const classification = byName.get(fixture.classify_column_name);
  assert(
    classification.metadata?.column_id === fixture.payload_column_id &&
      asArray(classification.metadata?.labels).length === 2,
    "Classification metadata did not preserve labels/source column.",
  );
  const entities = byName.get(fixture.entities_column_name);
  assert(
    entities.metadata?.column_id === fixture.payload_column_id &&
      entities.metadata?.instruction === "Extract names",
    "Entity extraction metadata did not preserve instruction/source column.",
  );
  const vector = byName.get(fixture.vector_column_name);
  assert(
    vector.metadata?.column_id === fixture.payload_column_id &&
      vector.metadata?.sub_type === "pinecone",
    "Vector DB metadata did not preserve source column/sub_type.",
  );
}

async function seedDatasetSyntheticFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const columnId = randomUUID();
  const datasetName = `api journey synthetic ${runId}`;
  const columnName = `synthetic_answer_${suffix}`;
  const originalDescription = `Original synthetic description ${suffix}`;
  const updatedDescription = `Updated synthetic description ${suffix}`;
  const syntheticColumnPayload = {
    name: columnName,
    data_type: "text",
    description: "Synthetic answer",
    property: "answer",
  };
  const syntheticAddColumnPayload = {
    ...syntheticColumnPayload,
    skip: false,
    is_new: false,
  };
  const syntheticConfig = {
    num_rows: 10,
    columns: [syntheticColumnPayload],
    dataset: {
      name: datasetName,
      description: originalDescription,
      objective: "Seed synthetic config for API journey",
      patterns: [],
    },
  };
  const columnConfig = {
    [columnId]: { is_visible: true, is_frozen: null },
  };
  const rowValues = Array.from({ length: 10 }, (_, index) => {
    const rowId = randomUUID();
    return {
      rowSql: `(
        ${sqlUuid(rowId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${index},
        ${sqlUuid(datasetId)},
        '{}'::jsonb
      )`,
      cellSql: `(
        ${sqlUuid(randomUUID())},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(`synthetic seeded value ${index} ${suffix}`)},
        ${sqlUuid(columnId)},
        ${sqlUuid(datasetId)},
        ${sqlUuid(rowId)},
        'pass',
        '{}'::jsonb,
        '{}'::jsonb,
        '{}'::jsonb,
        NULL::integer,
        NULL::integer,
        NULL::double precision
      )`,
    };
  });
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(columnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    ${sqlJsonLiteral(syntheticConfig)},
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(columnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(columnName)},
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues.map((row) => row.rowSql).join(",\n")}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${rowValues.map((row) => row.cellSql).join(",\n")}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL::text"},
  'column_id', ${sqlUuid(columnId)}::text,
  'column_name', ${sqlTextLiteral(columnName)},
  'original_description', ${sqlTextLiteral(originalDescription)},
  'updated_description', ${sqlTextLiteral(updatedDescription)},
  'synthetic_column_payload', ${sqlJsonLiteral(syntheticColumnPayload)},
  'synthetic_add_column_payload', ${sqlJsonLiteral(syntheticAddColumnPayload)},
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function loadDatasetSyntheticFixtureAudit(fixture) {
  assert(isUuid(fixture.dataset_id), "dataset_id must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'workspace_id', d.workspace_id::text,
  'synthetic_description', d.synthetic_dataset_config->'dataset'->>'description',
  'synthetic_num_rows', (d.synthetic_dataset_config->>'num_rows')::integer,
  'column_order_length', COALESCE(array_length(d.column_order, 1), 0),
  'row_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.dataset_id = d.id
      AND r.deleted = false
  ),
  'column_count', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.dataset_id = d.id
      AND c.deleted = false
  ),
  'cell_count', (
    SELECT count(*)
    FROM model_hub_cell c
    WHERE c.dataset_id = d.id
      AND c.deleted = false
  ),
  'column_statuses', (
    SELECT COALESCE(json_agg(DISTINCT c.status), '[]'::json)
    FROM model_hub_column c
    WHERE c.dataset_id = d.id
      AND c.deleted = false
  )
)
FROM model_hub_dataset d
WHERE d.id = ${sqlUuid(fixture.dataset_id)}
  AND d.deleted = false;
`;
  return runPostgresJson(sql);
}

function assertDatasetSyntheticFixtureAudit(audit, fixture) {
  assert(
    audit.workspace_id === fixture.workspace_id,
    "Synthetic fixture workspace id mismatch.",
  );
  assert(
    audit.synthetic_description === fixture.updated_description,
    "Synthetic config description was not updated.",
  );
  assert(
    Number(audit.synthetic_num_rows) === 10,
    "Synthetic config row count was not preserved.",
  );
  assert(Number(audit.row_count) === 10, "Synthetic row count changed.");
  assert(Number(audit.column_count) === 1, "Synthetic column count changed.");
  assert(Number(audit.cell_count) === 10, "Synthetic cell count changed.");
  assert(
    Number(audit.column_order_length) === 1,
    "Synthetic column order length changed.",
  );
}

async function seedDatasetExplanationFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const datasetName = `api journey explanation summary ${runId}`;
  const rowIds = [randomUUID(), randomUUID()];
  const rowSql = rowIds
    .map(
      (rowId, index) => `(
        ${sqlUuid(rowId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${index},
        ${sqlUuid(datasetId)},
        '{}'::jsonb
      )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    '{}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowSql}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL::text"},
  'row_count', (SELECT count(*) FROM inserted_rows)
);
`;
  return runPostgresJson(sql);
}

async function loadDatasetExplanationFixtureAudit(fixture) {
  assert(isUuid(fixture.dataset_id), "dataset_id must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'workspace_id', d.workspace_id::text,
  'eval_reason_status', d.eval_reason_status,
  'row_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.dataset_id = d.id
      AND r.deleted = false
  )
)
FROM model_hub_dataset d
WHERE d.id = ${sqlUuid(fixture.dataset_id)}
  AND d.deleted = false;
`;
  return runPostgresJson(sql);
}

function assertDatasetExplanationFixtureAudit(audit, fixture) {
  assert(
    audit.workspace_id === fixture.workspace_id,
    "Explanation summary fixture workspace id mismatch.",
  );
  assert(
    audit.eval_reason_status === "insufficient_data",
    "Explanation summary refresh did not persist insufficient_data status.",
  );
  assert(
    Number(audit.row_count) === fixture.row_count,
    "Explanation summary fixture row count changed.",
  );
}

function assertDatasetEvalStructure(
  payload,
  { metricId, templateId, expectedName, expectedMapping },
) {
  const evalData = payload?.eval || payload;
  assert(evalData && typeof evalData === "object", "Eval structure was empty.");
  if (metricId) {
    assert(
      evalData.id === metricId,
      "Eval structure returned wrong metric id.",
    );
  }
  assert(
    evalData.template_id === templateId,
    "Eval structure returned wrong template id.",
  );
  assert(evalData.name === expectedName, "Eval structure returned wrong name.");
  for (const [key, value] of Object.entries(expectedMapping || {})) {
    assert(
      String(evalData.mapping?.[key] ?? "") === String(value),
      `Eval structure mapping for ${key} did not match.`,
    );
  }
  assert(
    payloadArray(evalData.required_keys, "required_keys").includes("text"),
    "Eval structure did not expose required key text.",
  );
}

async function seedDatasetEvalDrawerFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const datasetName = `api journey eval drawer ${runId}`;
  const inputColumnName = `api_eval_text_${suffix}`;
  const rowTexts = [
    `alpha beta gamma ${suffix}`,
    `delta epsilon zeta ${suffix}`,
    `eta theta iota ${suffix}`,
  ];
  const rows = rowTexts.map((text, index) => ({
    rowId: randomUUID(),
    cellId: randomUUID(),
    text,
    order: index,
  }));
  const columnConfig = {
    [inputColumnId]: { is_visible: true, is_frozen: null },
  };
  const rowValues = rows
    .map(
      (row) => `(
        ${sqlUuid(row.rowId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${row.order},
        ${sqlUuid(datasetId)},
        '{}'::jsonb
      )`,
    )
    .join(",\n");
  const cellValues = rows
    .map(
      (row) => `(
        ${sqlUuid(row.cellId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(row.text)},
        '{}'::jsonb,
        '{}'::jsonb,
        '{}'::jsonb,
        'pass',
        ${sqlUuid(datasetId)},
        ${sqlUuid(inputColumnId)},
        ${sqlUuid(row.rowId)}
      )`,
    )
    .join(",\n");
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(inputColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(inputColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(inputColumnName)},
    'text',
    'OTHERS',
    NULL::varchar,
    ${sqlUuid(datasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowValues}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    value_infos,
    feedback_info,
    column_metadata,
    status,
    dataset_id,
    column_id,
    row_id
  )
  VALUES
${cellValues}
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'input_column_name', ${sqlTextLiteral(inputColumnName)},
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL::text"},
  'row_count', (SELECT count(*) FROM inserted_rows),
  'cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteDatasetEvalDrawerFixture(fixture) {
  assert(
    fixture && isUuid(fixture.dataset_id),
    "Dataset eval drawer fixture id must be a UUID for DB cleanup.",
  );
  const sql = `
WITH deleted_pending_tasks AS (
  DELETE FROM model_hub_pendingrowtask
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
  RETURNING 1
),
deleted_metrics AS (
  DELETE FROM model_hub_userevalmetric
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
  RETURNING 1
)
SELECT json_build_object(
  'deleted_pending_task_count', (SELECT count(*) FROM deleted_pending_tasks),
  'deleted_metric_count', (SELECT count(*) FROM deleted_metrics)
);
`;
  await runPostgresJson(sql);
  return hardDeleteDatasetCopyFixture([fixture.dataset_id]);
}

async function seedLegacyEvalUserTemplateFixture({
  runId,
  organizationId,
  workspaceId,
  templateId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const activeWorkspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const otherWorkspaceId = await findOtherWorkspaceId(
    organizationId,
    workspaceId,
  );
  const datasetId = randomUUID();
  const outsideDatasetId = randomUUID();
  const inputColumnId = randomUUID();
  const outsideColumnId = randomUUID();
  const rowOneId = randomUUID();
  const rowTwoId = randomUUID();
  const outsideRowId = randomUUID();
  const inputCellOneId = randomUUID();
  const inputCellTwoId = randomUUID();
  const outsideInputCellId = randomUUID();
  const otherDatasetId = otherWorkspaceId ? randomUUID() : null;
  const otherColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherRowId = otherWorkspaceId ? randomUUID() : null;
  const otherInputCellId = otherWorkspaceId ? randomUUID() : null;
  const otherMetricId = otherWorkspaceId ? randomUUID() : null;
  const otherEvalColumnId = otherWorkspaceId ? randomUUID() : null;
  const otherEvalCellId = otherWorkspaceId ? randomUUID() : null;
  const datasetName = `api journey legacy eval user ${runId}`;
  const outsideDatasetName = `api journey legacy eval outside ${runId}`;
  const inputColumnName = `api_legacy_eval_input_${suffix}`;
  const outsideColumnName = `api_legacy_eval_outside_${suffix}`;
  const inputValueOne = `alpha beta gamma ${suffix}`;
  const inputValueTwo = `delta epsilon zeta ${suffix}`;
  const outsideInputValue = `outside row ${suffix}`;
  const evalValueOne = `old eval one ${suffix}`;
  const evalValueTwo = `old eval two ${suffix}`;
  const outsideEvalValue = `outside old eval ${suffix}`;
  const otherInputValue = `other workspace row ${suffix}`;
  const otherEvalValue = `other workspace old eval ${suffix}`;
  const datasetValues = [
    {
      id: datasetId,
      name: datasetName,
      workspaceSql: activeWorkspaceSql,
      columnIds: [inputColumnId],
    },
    {
      id: outsideDatasetId,
      name: outsideDatasetName,
      workspaceSql: activeWorkspaceSql,
      columnIds: [outsideColumnId],
    },
  ];
  if (otherWorkspaceId) {
    datasetValues.push({
      id: otherDatasetId,
      name: `api journey legacy eval other ${runId}`,
      workspaceSql: sqlUuid(otherWorkspaceId),
      columnIds: [otherColumnId],
    });
  }
  const datasetSql = datasetValues
    .map(
      (dataset) => `(
        ${sqlUuid(dataset.id)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(dataset.name)},
        ARRAY[${dataset.columnIds.map(sqlTextLiteral).join(", ")}]::varchar[],
        'GenerativeLLM',
        ${sqlUuid(organizationId)},
        ${dataset.workspaceSql},
        'build',
        ${sqlJsonLiteral(
          Object.fromEntries(
            dataset.columnIds.map((columnId) => [
              columnId,
              { is_visible: true, is_frozen: null },
            ]),
          ),
        )},
        '{}'::jsonb,
        '{}'::jsonb,
        '[]'::jsonb,
        'pending'
      )`,
    )
    .join(",\n");
  const columnRows = [
    [inputColumnId, inputColumnName, datasetId],
    [outsideColumnId, outsideColumnName, outsideDatasetId],
  ];
  if (otherWorkspaceId) {
    columnRows.push([
      otherColumnId,
      `api_legacy_eval_other_${suffix}`,
      otherDatasetId,
    ]);
  }
  const columnSql = columnRows
    .map(
      ([columnId, name, datasetIdForColumn]) => `(
        ${sqlUuid(columnId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(name)},
        'text',
        'OTHERS',
        NULL::varchar,
        ${sqlUuid(datasetIdForColumn)},
        '{}'::jsonb,
        'Completed'
      )`,
    )
    .join(",\n");
  const rowRows = [
    [rowOneId, datasetId, 0],
    [rowTwoId, datasetId, 1],
    [outsideRowId, outsideDatasetId, 0],
  ];
  if (otherWorkspaceId) rowRows.push([otherRowId, otherDatasetId, 0]);
  const rowSql = rowRows
    .map(
      ([rowId, rowDatasetId, order]) => `(
        ${sqlUuid(rowId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${order},
        ${sqlUuid(rowDatasetId)},
        '{}'::jsonb
      )`,
    )
    .join(",\n");
  const cellRows = [
    [inputCellOneId, inputValueOne, inputColumnId, datasetId, rowOneId],
    [inputCellTwoId, inputValueTwo, inputColumnId, datasetId, rowTwoId],
    [
      outsideInputCellId,
      outsideInputValue,
      outsideColumnId,
      outsideDatasetId,
      outsideRowId,
    ],
  ];
  if (otherWorkspaceId) {
    cellRows.push([
      otherInputCellId,
      otherInputValue,
      otherColumnId,
      otherDatasetId,
      otherRowId,
    ]);
  }
  const cellSql = cellRows
    .map(
      ([cellId, value, columnId, cellDatasetId, rowId]) => `(
        ${sqlUuid(cellId)},
        now(),
        now(),
        false,
        NULL::timestamptz,
        ${sqlTextLiteral(value)},
        ${sqlUuid(columnId)},
        ${sqlUuid(cellDatasetId)},
        ${sqlUuid(rowId)},
        'pass',
        '{}'::jsonb,
        '{}'::jsonb,
        '{}'::jsonb,
        NULL::integer,
        NULL::integer,
        NULL::double precision
      )`,
    )
    .join(",\n");
  const otherWorkspaceSql = otherWorkspaceId
    ? `,
inserted_other_metric AS (
  INSERT INTO model_hub_userevalmetric (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    config,
    dataset_id,
    template_id,
    organization_id,
    workspace_id,
    status,
    show_in_sidebar,
    source_id,
    column_deleted,
    user_id,
    error_localizer,
    kb_id,
    model,
    eval_group_id,
    composite_weight_overrides
  )
  VALUES (
    ${sqlUuid(otherMetricId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(`api legacy eval other metric ${runId}`)},
    ${sqlJsonLiteral({ mapping: { text: otherColumnId }, params: {} })},
    ${sqlUuid(otherDatasetId)},
    ${sqlUuid(templateId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(otherWorkspaceId)},
    'Inactive',
    true,
    '',
    false,
    NULL::uuid,
    false,
    NULL::uuid,
    'turing_small',
    NULL::uuid,
    NULL::jsonb
  )
  RETURNING id
),
inserted_other_eval_column AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES (
    ${sqlUuid(otherEvalColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(`api_legacy_eval_other_output_${suffix}`)},
    'text',
    'evaluation',
    ${sqlTextLiteral(otherMetricId)},
    ${sqlUuid(otherDatasetId)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_other_eval_cell AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES (
    ${sqlUuid(otherEvalCellId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(otherEvalValue)},
    ${sqlUuid(otherEvalColumnId)},
    ${sqlUuid(otherDatasetId)},
    ${sqlUuid(otherRowId)},
    'pass',
    ${sqlJsonLiteral({ kept: true })},
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )
  RETURNING id
)`
    : "";
  const sql = `
WITH inserted_datasets AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES
${datasetSql}
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
${columnSql}
  RETURNING id
),
inserted_rows AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES
${rowSql}
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
${cellSql}
  RETURNING id
)${otherWorkspaceSql}
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_datasets) >= 2,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'row_one_id', ${sqlUuid(rowOneId)}::text,
  'row_two_id', ${sqlUuid(rowTwoId)}::text,
  'outside_dataset_id', ${sqlUuid(outsideDatasetId)}::text,
  'outside_column_id', ${sqlUuid(outsideColumnId)}::text,
  'outside_row_id', ${sqlUuid(outsideRowId)}::text,
  'other_workspace_id', ${otherWorkspaceId ? `${sqlUuid(otherWorkspaceId)}::text` : "NULL::text"},
  'other_dataset_id', ${otherDatasetId ? `${sqlUuid(otherDatasetId)}::text` : "NULL::text"},
  'other_column_id', ${otherColumnId ? `${sqlUuid(otherColumnId)}::text` : "NULL::text"},
  'other_row_id', ${otherRowId ? `${sqlUuid(otherRowId)}::text` : "NULL::text"},
  'other_metric_id', ${otherMetricId ? `${sqlUuid(otherMetricId)}::text` : "NULL::text"},
  'eval_value_one', ${sqlTextLiteral(evalValueOne)},
  'eval_value_two', ${sqlTextLiteral(evalValueTwo)},
  'outside_eval_value', ${sqlTextLiteral(outsideEvalValue)},
  'other_eval_value', ${sqlTextLiteral(otherEvalValue)},
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL::text"},
  'inserted_dataset_count', (SELECT count(*) FROM inserted_datasets),
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_row_count', (SELECT count(*) FROM inserted_rows),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells)
);
`;
  return runPostgresJson(sql);
}

async function seedLegacyEvalUserTemplateEvaluationCells({
  fixture,
  metricId,
}) {
  assert(
    fixture && isUuid(fixture.dataset_id) && isUuid(fixture.outside_dataset_id),
    "Legacy eval-user-template fixture ids must be UUIDs for DB seeding.",
  );
  assert(isUuid(metricId), "metricId must be a UUID for DB seeding.");
  const evalColumnId = randomUUID();
  const outsideEvalColumnId = randomUUID();
  const evalCellOneId = randomUUID();
  const evalCellTwoId = randomUUID();
  const outsideEvalCellId = randomUUID();
  const sql = `
WITH inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
  (
    ${sqlUuid(evalColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'api legacy eval output',
    'text',
    'evaluation',
    ${sqlTextLiteral(metricId)},
    ${sqlUuid(fixture.dataset_id)},
    '{}'::jsonb,
    'Completed'
  ),
  (
    ${sqlUuid(outsideEvalColumnId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    'api legacy outside eval output',
    'text',
    'evaluation',
    ${sqlTextLiteral(metricId)},
    ${sqlUuid(fixture.outside_dataset_id)},
    '{}'::jsonb,
    'Completed'
  )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    column_id,
    dataset_id,
    row_id,
    status,
    value_infos,
    feedback_info,
    column_metadata,
    completion_tokens,
    prompt_tokens,
    response_time
  )
  VALUES
  (
    ${sqlUuid(evalCellOneId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(fixture.eval_value_one)},
    ${sqlUuid(evalColumnId)},
    ${sqlUuid(fixture.dataset_id)},
    ${sqlUuid(fixture.row_one_id)},
    'pass',
    ${sqlJsonLiteral({ seeded: true, row: 1 })},
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  ),
  (
    ${sqlUuid(evalCellTwoId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(fixture.eval_value_two)},
    ${sqlUuid(evalColumnId)},
    ${sqlUuid(fixture.dataset_id)},
    ${sqlUuid(fixture.row_two_id)},
    'pass',
    ${sqlJsonLiteral({ seeded: true, row: 2 })},
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  ),
  (
    ${sqlUuid(outsideEvalCellId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(fixture.outside_eval_value)},
    ${sqlUuid(outsideEvalColumnId)},
    ${sqlUuid(fixture.outside_dataset_id)},
    ${sqlUuid(fixture.outside_row_id)},
    'pass',
    ${sqlJsonLiteral({ kept: true })},
    '{}'::jsonb,
    '{}'::jsonb,
    NULL::integer,
    NULL::integer,
    NULL::double precision
  )
  RETURNING id
)
SELECT json_build_object(
  'inserted_eval_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_eval_cell_count', (SELECT count(*) FROM inserted_cells),
  'eval_column_id', ${sqlUuid(evalColumnId)}::text,
  'outside_eval_column_id', ${sqlUuid(outsideEvalColumnId)}::text
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteLegacyEvalUserTemplateFixture(fixture) {
  assert(
    fixture && isUuid(fixture.dataset_id) && isUuid(fixture.outside_dataset_id),
    "Legacy eval-user-template fixture ids must be UUIDs for DB cleanup.",
  );
  const datasetIds = [fixture.dataset_id, fixture.outside_dataset_id].filter(
    isUuid,
  );
  if (isUuid(fixture.other_dataset_id))
    datasetIds.push(fixture.other_dataset_id);
  const sql = `
WITH deleted_pending_tasks AS (
  DELETE FROM model_hub_pendingrowtask
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_metrics AS (
  DELETE FROM model_hub_userevalmetric
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_cells AS (
  DELETE FROM model_hub_cell
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_rows AS (
  DELETE FROM model_hub_row
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_columns AS (
  DELETE FROM model_hub_column
  WHERE dataset_id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
),
deleted_datasets AS (
  DELETE FROM model_hub_dataset
  WHERE id = ANY(${sqlUuidArray(datasetIds)})
  RETURNING 1
)
SELECT json_build_object(
  'deleted_pending_task_count', (SELECT count(*) FROM deleted_pending_tasks),
  'deleted_metric_count', (SELECT count(*) FROM deleted_metrics),
  'deleted_cell_count', (SELECT count(*) FROM deleted_cells),
  'deleted_row_count', (SELECT count(*) FROM deleted_rows),
  'deleted_column_count', (SELECT count(*) FROM deleted_columns),
  'deleted_dataset_count', (SELECT count(*) FROM deleted_datasets)
);
`;
  return runPostgresJson(sql);
}

async function loadLegacyEvalUserTemplateAudit({
  fixture,
  templateId,
  metricName,
  organizationId,
  workspaceId,
}) {
  assert(
    fixture && isUuid(fixture.dataset_id) && isUuid(fixture.outside_dataset_id),
    "Legacy eval-user-template fixture ids must be UUIDs for DB audit.",
  );
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const otherMetricStatus = isUuid(fixture.other_metric_id)
    ? `(SELECT c.status FROM model_hub_cell c JOIN model_hub_column col ON col.id = c.column_id WHERE c.row_id = ${sqlUuid(fixture.other_row_id)} AND c.dataset_id = ${sqlUuid(fixture.other_dataset_id)} AND col.source_id = ${sqlTextLiteral(fixture.other_metric_id)} AND c.deleted = false LIMIT 1)`
    : "NULL::varchar";
  const otherMetricValue = isUuid(fixture.other_metric_id)
    ? `(SELECT c.value FROM model_hub_cell c JOIN model_hub_column col ON col.id = c.column_id WHERE c.row_id = ${sqlUuid(fixture.other_row_id)} AND c.dataset_id = ${sqlUuid(fixture.other_dataset_id)} AND col.source_id = ${sqlTextLiteral(fixture.other_metric_id)} AND c.deleted = false LIMIT 1)`
    : "NULL::text";
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(fixture.dataset_id)}::uuid AS dataset_id,
    ${sqlUuid(fixture.outside_dataset_id)}::uuid AS outside_dataset_id,
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlTextLiteral(metricName)}::text AS metric_name
),
metric AS (
  SELECT uem.*
  FROM requested
  JOIN model_hub_userevalmetric uem
    ON uem.dataset_id = requested.dataset_id
   AND uem.template_id = requested.template_id
   AND uem.name = requested.metric_name
   AND uem.organization_id = ${sqlUuid(organizationId)}
   AND uem.deleted = false
  ORDER BY uem.created_at DESC
  LIMIT 1
),
eval_cells AS (
  SELECT
    c.row_id::text,
    c.status,
    c.value,
    c.value_infos,
    r."order"
  FROM metric m
  JOIN model_hub_column col
    ON col.dataset_id = m.dataset_id
   AND col.source = 'evaluation'
   AND col.source_id = m.id::text
   AND col.deleted = false
  JOIN model_hub_cell c
    ON c.column_id = col.id
   AND c.dataset_id = m.dataset_id
   AND c.deleted = false
  JOIN model_hub_row r
    ON r.id = c.row_id
   AND r.dataset_id = m.dataset_id
   AND r.deleted = false
),
outside_eval_cell AS (
  SELECT c.status, c.value
  FROM metric m
  JOIN requested req ON true
  JOIN model_hub_column col
    ON col.dataset_id = req.outside_dataset_id
   AND col.source = 'evaluation'
   AND col.source_id = m.id::text
   AND col.deleted = false
  JOIN model_hub_cell c
    ON c.column_id = col.id
   AND c.dataset_id = req.outside_dataset_id
   AND c.row_id = ${sqlUuid(fixture.outside_row_id)}
   AND c.deleted = false
  LIMIT 1
)
SELECT json_build_object(
  'dataset_id', d.id::text,
  'workspace_id', d.workspace_id::text,
  'template_id', requested.template_id::text,
  'metric_id', (SELECT id::text FROM metric),
  'metric_name', (SELECT name FROM metric),
  'metric_organization_id', (SELECT organization_id::text FROM metric),
  'metric_workspace_id', (SELECT workspace_id::text FROM metric),
  'metric_dataset_id', (SELECT dataset_id::text FROM metric),
  'metric_template_id', (SELECT template_id::text FROM metric),
  'metric_config', (SELECT config FROM metric),
  'metric_model', (SELECT model FROM metric),
  'active_metric_count', (
    SELECT count(*)
    FROM model_hub_userevalmetric uem
    WHERE uem.dataset_id = requested.dataset_id
      AND uem.template_id = requested.template_id
      AND uem.name = requested.metric_name
      AND uem.organization_id = ${sqlUuid(organizationId)}
      AND uem.deleted = false
  ),
  'failed_candidate_metric_count', (
    SELECT count(*)
    FROM model_hub_userevalmetric uem
    WHERE uem.dataset_id = requested.dataset_id
      AND uem.name LIKE ${sqlTextLiteral("api_legacy_user_dup_%")}
      AND uem.organization_id = ${sqlUuid(organizationId)}
      AND uem.deleted = false
  ),
  'eval_cells', COALESCE((
    SELECT json_agg(
      json_build_object(
        'row_id', row_id,
        'status', status,
        'value', value,
        'value_infos', value_infos
      )
      ORDER BY "order"
    )
    FROM eval_cells
  ), '[]'::json),
  'running_eval_cell_count', (
    SELECT count(*) FROM eval_cells WHERE status = 'running'
  ),
  'outside_eval_cell_status', (SELECT status FROM outside_eval_cell),
  'outside_eval_cell_value', (SELECT value FROM outside_eval_cell),
  'other_eval_cell_status', ${otherMetricStatus},
  'other_eval_cell_value', ${otherMetricValue}
)
FROM requested
LEFT JOIN model_hub_dataset d
  ON d.id = requested.dataset_id;
`;
  return runPostgresJson(sql);
}

function assertLegacyEvalUserTemplateCreateAudit(
  audit,
  {
    fixture,
    templateId,
    metricName,
    organizationId,
    workspaceId,
    expectedParams,
  },
) {
  assert(
    audit?.dataset_id === fixture.dataset_id,
    "Legacy eval-user-template audit dataset mismatch.",
  );
  assert(
    audit.template_id === templateId,
    "Legacy eval-user-template audit template mismatch.",
  );
  assert(
    isUuid(audit.metric_id),
    "Legacy eval-user-template audit did not find the created metric.",
  );
  assert(
    Number(audit.active_metric_count) === 1,
    "Legacy eval-user-template create did not leave exactly one active metric.",
  );
  assert(
    Number(audit.failed_candidate_metric_count) === 0,
    "Legacy eval-user-template failed guard inserted a candidate metric.",
  );
  assert(
    audit.metric_name === metricName,
    "Legacy eval-user-template audit metric name mismatch.",
  );
  assert(
    audit.metric_organization_id === organizationId,
    "Legacy eval-user-template audit organization mismatch.",
  );
  assert(
    audit.metric_dataset_id === fixture.dataset_id,
    "Legacy eval-user-template audit metric dataset mismatch.",
  );
  assert(
    audit.metric_template_id === templateId,
    "Legacy eval-user-template audit metric template mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.workspace_id === workspaceId,
      "Legacy eval-user-template audit dataset workspace mismatch.",
    );
    assert(
      audit.metric_workspace_id === workspaceId,
      "Legacy eval-user-template audit metric workspace mismatch.",
    );
  }
  assert(
    audit.metric_model === "turing_small",
    "Legacy eval-user-template create did not persist requested model.",
  );
  assert(
    String(audit.metric_config?.mapping?.text || "") ===
      fixture.input_column_id,
    "Legacy eval-user-template create did not persist the scoped column mapping.",
  );
  const actualParams = audit.metric_config?.params || {};
  const expectedParamEntries = Object.entries(expectedParams || {});
  assert(
    Object.keys(actualParams).length === expectedParamEntries.length,
    "Legacy eval-user-template create persisted an unexpected params shape.",
  );
  for (const [key, value] of expectedParamEntries) {
    assert(
      String(actualParams[key]) === String(value),
      "Legacy eval-user-template create did not persist params config.",
    );
  }
}

function assertLegacyEvalCellState(audit, rowId, expected) {
  const cell = payloadArray(audit?.eval_cells, "eval_cells").find(
    (candidate) => candidate?.row_id === rowId,
  );
  assert(
    isUuid(cell?.row_id),
    "Legacy evaluate-rows audit did not find eval cell.",
  );
  if (Object.hasOwn(expected, "status")) {
    assert(
      cell.status === expected.status,
      `Legacy evaluate-rows cell ${rowId} status mismatch.`,
    );
  }
  if (Object.hasOwn(expected, "value")) {
    assert(
      cell.value === expected.value,
      `Legacy evaluate-rows cell ${rowId} value mismatch.`,
    );
  }
  if (Object.hasOwn(expected, "valueInfos")) {
    assert(
      JSON.stringify(cell.value_infos || {}) ===
        JSON.stringify(expected.valueInfos || {}),
      `Legacy evaluate-rows cell ${rowId} value_infos mismatch.`,
    );
  }
}

async function loadDatasetEvalDrawerDbAudit({
  datasetId,
  templateId,
  metricName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(datasetId)}::uuid AS dataset_id,
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlTextLiteral(metricName)}::text AS metric_name
),
metric AS (
  SELECT uem.*
  FROM requested
  JOIN model_hub_userevalmetric uem
    ON uem.dataset_id = requested.dataset_id
   AND uem.template_id = requested.template_id
   AND uem.name = requested.metric_name
   AND uem.organization_id = ${sqlUuid(organizationId)}
  ORDER BY uem.created_at DESC
  LIMIT 1
),
eval_columns AS (
  SELECT c.*
  FROM metric m
  JOIN model_hub_column c
    ON c.dataset_id = m.dataset_id
   AND c.source = 'evaluation'
   AND c.source_id = m.id::text
),
reason_columns AS (
  SELECT rc.*
  FROM metric m
  JOIN eval_columns ec
    ON true
  JOIN model_hub_column rc
    ON rc.dataset_id = m.dataset_id
   AND rc.source = 'evaluation_reason'
   AND rc.source_id = ec.id::text || '-sourceid-' || m.id::text
)
SELECT json_build_object(
  'dataset_id', d.id::text,
  'dataset_workspace_id', d.workspace_id::text,
  'template_id', requested.template_id::text,
  'metric_exists', EXISTS(SELECT 1 FROM metric),
  'metric_id', (SELECT id::text FROM metric),
  'metric_name', (SELECT name FROM metric),
  'metric_organization_id', (SELECT organization_id::text FROM metric),
  'metric_workspace_id', (SELECT workspace_id::text FROM metric),
  'metric_status', (SELECT status FROM metric),
  'metric_config', (SELECT config FROM metric),
  'metric_deleted', (SELECT deleted FROM metric),
  'metric_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM metric),
  'active_metric_count', (
    SELECT count(*)
    FROM model_hub_userevalmetric uem
    WHERE uem.dataset_id = requested.dataset_id
      AND uem.template_id = requested.template_id
      AND uem.name = requested.metric_name
      AND uem.organization_id = ${sqlUuid(organizationId)}
      AND uem.deleted = false
  ),
  'active_eval_column_count', (
    SELECT count(*) FROM eval_columns WHERE deleted = false
  ),
  'deleted_eval_column_count', (
    SELECT count(*) FROM eval_columns WHERE deleted = true
  ),
  'deleted_eval_column_deleted_at_count', (
    SELECT count(*) FROM eval_columns WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'active_reason_column_count', (
    SELECT count(*) FROM reason_columns WHERE deleted = false
  ),
  'deleted_reason_column_count', (
    SELECT count(*) FROM reason_columns WHERE deleted = true
  ),
  'deleted_reason_column_deleted_at_count', (
    SELECT count(*) FROM reason_columns WHERE deleted = true AND deleted_at IS NOT NULL
  ),
  'column_order_contains_active_eval', EXISTS(
    SELECT 1
    FROM eval_columns c
    WHERE c.deleted = false AND c.id::text = ANY(d.column_order)
  ),
  'column_order_contains_active_reason', EXISTS(
    SELECT 1
    FROM reason_columns c
    WHERE c.deleted = false AND c.id::text = ANY(d.column_order)
  )
)
FROM requested
LEFT JOIN model_hub_dataset d
  ON d.id = requested.dataset_id;
`;
  return runPostgresJson(sql);
}

function assertDatasetEvalDrawerDbAudit(
  audit,
  {
    datasetId,
    templateId,
    metricId,
    metricName,
    organizationId,
    workspaceId,
    expectedStatus,
    expectedDeleted,
    expectedEvalColumns = 0,
    expectedReasonColumns = 0,
    expectedDeletedEvalColumns = 0,
    expectedDeletedReasonColumns = 0,
  },
) {
  assert(
    audit?.dataset_id === datasetId,
    "Eval drawer audit dataset mismatch.",
  );
  assert(
    audit.template_id === templateId,
    "Eval drawer audit template mismatch.",
  );
  assert(
    audit.metric_exists === true,
    "Eval drawer audit did not find metric.",
  );
  assert(audit.metric_id === metricId, "Eval drawer audit metric id mismatch.");
  assert(
    audit.metric_name === metricName,
    "Eval drawer audit metric name mismatch.",
  );
  assert(
    audit.metric_organization_id === organizationId,
    "Eval drawer audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.dataset_workspace_id === workspaceId,
      "Eval drawer audit dataset workspace mismatch.",
    );
    assert(
      audit.metric_workspace_id === workspaceId,
      "Eval drawer audit metric workspace mismatch.",
    );
  }
  assert(
    audit.metric_status === expectedStatus,
    "Eval drawer audit metric status mismatch.",
  );
  assert(
    audit.metric_deleted === expectedDeleted,
    "Eval drawer audit deleted state mismatch.",
  );
  assert(
    String(audit.metric_config?.mapping?.text || ""),
    "Eval drawer audit metric mapping was empty.",
  );
  if (expectedDeleted) {
    assert(
      audit.metric_deleted_at_set === true,
      "Deleted eval drawer metric missing deleted_at.",
    );
    assert(
      Number(audit.active_metric_count) === 0,
      "Deleted eval drawer metric still counted as active.",
    );
  } else {
    assert(
      Number(audit.active_metric_count) === 1,
      "Active eval drawer metric count mismatch.",
    );
  }
  assert(
    Number(audit.active_eval_column_count) === expectedEvalColumns,
    "Eval drawer active eval column count mismatch.",
  );
  assert(
    Number(audit.active_reason_column_count) === expectedReasonColumns,
    "Eval drawer active reason column count mismatch.",
  );
  assert(
    Number(audit.deleted_eval_column_count) === expectedDeletedEvalColumns,
    "Eval drawer deleted eval column count mismatch.",
  );
  assert(
    Number(audit.deleted_reason_column_count) === expectedDeletedReasonColumns,
    "Eval drawer deleted reason column count mismatch.",
  );
  if (expectedEvalColumns > 0) {
    assert(
      audit.column_order_contains_active_eval === true,
      "Eval drawer column_order missing active eval column.",
    );
  }
  if (expectedReasonColumns > 0) {
    assert(
      audit.column_order_contains_active_reason === true,
      "Eval drawer column_order missing active reason column.",
    );
  }
  if (expectedDeletedEvalColumns > 0) {
    assert(
      Number(audit.deleted_eval_column_deleted_at_count) >=
        expectedDeletedEvalColumns,
      "Deleted eval drawer eval column missing deleted_at.",
    );
  }
  if (expectedDeletedReasonColumns > 0) {
    assert(
      Number(audit.deleted_reason_column_deleted_at_count) >=
        expectedDeletedReasonColumns,
      "Deleted eval drawer reason column missing deleted_at.",
    );
  }
}

async function seedRunPromptEditFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const workspaceSql = workspaceId ? sqlUuid(workspaceId) : "NULL::uuid";
  const datasetId = randomUUID();
  const inputColumnId = randomUUID();
  const outputColumnId = randomUUID();
  const rowId = randomUUID();
  const inputCellId = randomUUID();
  const outputCellId = randomUUID();
  const runPromptId = randomUUID();
  const datasetName = `api journey run prompt edit ${runId}`;
  const inputColumnName = `api_edit_input_${suffix}`;
  const outputColumnName = `api_edit_output_${suffix}`;
  const editedOutputColumnName = `api_edit_output_renamed_${suffix}`;
  const modelName = "gpt-4o-mini";
  const inputValue = `Seeded prompt edit input ${runId}`;
  const outputValue = `seeded output ${runId}`;
  const messages = [
    {
      role: "system",
      content: [
        {
          type: "text",
          text: "Return exactly OK. Do not include punctuation.",
        },
      ],
    },
    {
      role: "user",
      content: [{ type: "text", text: `Input: {{${inputColumnName}}}` }],
    },
  ];
  const runPromptConfig = {
    temperature: 0,
    max_tokens: 20,
    template_format: "f-string",
    model_type: "llm",
  };
  const columnConfig = {
    [inputColumnId]: { is_visible: true, is_frozen: null },
    [outputColumnId]: { is_visible: true, is_frozen: null },
  };
  const sql = `
WITH inserted_dataset AS (
  INSERT INTO model_hub_dataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    column_order,
    model_type,
    organization_id,
    workspace_id,
    source,
    column_config,
    dataset_config,
    synthetic_dataset_config,
    eval_reasons,
    eval_reason_status
  )
  VALUES (
    ${sqlUuid(datasetId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(datasetName)},
    ARRAY[${sqlTextLiteral(inputColumnId)}, ${sqlTextLiteral(outputColumnId)}]::varchar[],
    'GenerativeLLM',
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    'build',
    ${sqlJsonLiteral(columnConfig)},
    '{}'::jsonb,
    '{}'::jsonb,
    '[]'::jsonb,
    'pending'
  )
  RETURNING id
),
inserted_columns AS (
  INSERT INTO model_hub_column (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    data_type,
    source,
    source_id,
    dataset_id,
    metadata,
    status
  )
  VALUES
    (
      ${sqlUuid(inputColumnId)},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(inputColumnName)},
      'text',
      'OTHERS',
      NULL::varchar,
      ${sqlUuid(datasetId)},
      '{}'::jsonb,
      'Completed'
    ),
    (
      ${sqlUuid(outputColumnId)},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(outputColumnName)},
      'text',
      'run_prompt',
      ${sqlTextLiteral(runPromptId)},
      ${sqlUuid(datasetId)},
      '{}'::jsonb,
      'Completed'
    )
  RETURNING id
),
inserted_row AS (
  INSERT INTO model_hub_row (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    "order",
    dataset_id,
    metadata
  )
  VALUES (
    ${sqlUuid(rowId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    0,
    ${sqlUuid(datasetId)},
    '{}'::jsonb
  )
  RETURNING id
),
inserted_cells AS (
  INSERT INTO model_hub_cell (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    value,
    value_infos,
    feedback_info,
    column_metadata,
    status,
    dataset_id,
    column_id,
    row_id
  )
  VALUES
    (
      ${sqlUuid(inputCellId)},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(inputValue)},
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      'pass',
      ${sqlUuid(datasetId)},
      ${sqlUuid(inputColumnId)},
      ${sqlUuid(rowId)}
    ),
    (
      ${sqlUuid(outputCellId)},
      now(),
      now(),
      false,
      NULL::timestamptz,
      ${sqlTextLiteral(outputValue)},
      '{}'::jsonb,
      '{}'::jsonb,
      '{}'::jsonb,
      'pass',
      ${sqlUuid(datasetId)},
      ${sqlUuid(outputColumnId)},
      ${sqlUuid(rowId)}
    )
  RETURNING id
),
inserted_run_prompt AS (
  INSERT INTO model_hub_runprompter (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    model,
    name,
    concurrency,
    messages,
    output_format,
    temperature,
    frequency_penalty,
    presence_penalty,
    max_tokens,
    top_p,
    response_format,
    tool_choice,
    status,
    dataset_id,
    organization_id,
    workspace_id,
    run_prompt_config
  )
  VALUES (
    ${sqlUuid(runPromptId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(modelName)},
    ${sqlTextLiteral(outputColumnName)},
    1,
    ARRAY[${messages.map(sqlJsonLiteral).join(", ")}]::jsonb[],
    'string',
    0,
    NULL::double precision,
    NULL::double precision,
    20,
    1,
    NULL::jsonb,
    NULL::varchar,
    'Completed',
    ${sqlUuid(datasetId)},
    ${sqlUuid(organizationId)},
    ${workspaceSql},
    ${sqlJsonLiteral(runPromptConfig)}
  )
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', (SELECT count(*) FROM inserted_dataset) = 1,
  'dataset_id', ${sqlUuid(datasetId)}::text,
  'dataset_name', ${sqlTextLiteral(datasetName)},
  'input_column_id', ${sqlUuid(inputColumnId)}::text,
  'input_column_name', ${sqlTextLiteral(inputColumnName)},
  'output_column_id', ${sqlUuid(outputColumnId)}::text,
  'output_column_name', ${sqlTextLiteral(outputColumnName)},
  'edited_output_column_name', ${sqlTextLiteral(editedOutputColumnName)},
  'model_name', ${sqlTextLiteral(modelName)},
  'run_prompt_id', ${sqlUuid(runPromptId)}::text,
  'row_id', ${sqlUuid(rowId)}::text,
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL::text"},
  'inserted_column_count', (SELECT count(*) FROM inserted_columns),
  'inserted_cell_count', (SELECT count(*) FROM inserted_cells),
  'inserted_run_prompt_count', (SELECT count(*) FROM inserted_run_prompt)
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteRunPromptEditFixture(fixture) {
  assert(
    fixture && isUuid(fixture.dataset_id) && isUuid(fixture.run_prompt_id),
    "Run-prompt edit fixture ids must be UUIDs for DB cleanup.",
  );
  const sql = `
WITH deleted_pending_tasks AS (
  DELETE FROM model_hub_pendingrowtask
  WHERE dataset_id = ${sqlUuid(fixture.dataset_id)}
  RETURNING 1
),
deleted_run_prompt_tools AS (
  DELETE FROM model_hub_runprompter_tools
  WHERE runprompter_id = ${sqlUuid(fixture.run_prompt_id)}
  RETURNING 1
),
deleted_run_prompt AS (
  DELETE FROM model_hub_runprompter
  WHERE id = ${sqlUuid(fixture.run_prompt_id)}
  RETURNING 1
)
SELECT json_build_object(
  'deleted_pending_task_count', (SELECT count(*) FROM deleted_pending_tasks),
  'deleted_run_prompt_tool_count', (SELECT count(*) FROM deleted_run_prompt_tools),
  'deleted_run_prompt_count', (SELECT count(*) FROM deleted_run_prompt)
);
`;
  await runPostgresJson(sql);
  return hardDeleteDatasetCopyFixture([fixture.dataset_id]);
}

async function loadDatasetOptimizationReadCandidate(
  organizationId,
  workspaceId,
) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspacePredicate = workspaceId
    ? `(d.workspace_id = ${sqlUuid(workspaceId)} OR d.workspace_id IS NULL)`
    : "true";
  const sql = `
WITH candidate AS (
  SELECT
    o.id,
    o.name,
    o.status,
    o.column_id,
    c.dataset_id,
    d.workspace_id,
    trial.id AS trial_id,
    (
      SELECT count(*)
      FROM dataset_optimization_step s
      WHERE s.optimization_run_id = o.id AND s.deleted = false
    ) AS step_count,
    (
      SELECT count(*)
      FROM dataset_optimization_trial t
      WHERE t.optimization_run_id = o.id AND t.deleted = false
    ) AS trial_count
  FROM model_hub_optimizedataset o
  JOIN model_hub_column c ON c.id = o.column_id AND c.deleted = false
  JOIN model_hub_dataset d ON d.id = c.dataset_id AND d.deleted = false
  JOIN LATERAL (
    SELECT t.id
    FROM dataset_optimization_trial t
    WHERE t.optimization_run_id = o.id AND t.deleted = false
    ORDER BY t.is_baseline ASC, t.trial_number ASC
    LIMIT 1
  ) trial ON true
  WHERE o.deleted = false
    AND o.optimizer_algorithm IS NOT NULL
    AND d.organization_id = ${sqlUuid(organizationId)}
    AND ${workspacePredicate}
  ORDER BY
    CASE WHEN o.status = 'completed' THEN 0 ELSE 1 END,
    o.created_at DESC
  LIMIT 1
)
SELECT COALESCE(
  (
    SELECT json_build_object(
      'optimization_id', id::text,
      'optimization_name', name,
      'status', status,
      'column_id', column_id::text,
      'dataset_id', dataset_id::text,
      'workspace_id', workspace_id::text,
      'trial_id', trial_id::text,
      'step_count', step_count,
      'trial_count', trial_count
    )
    FROM candidate
  ),
  'null'::json
);
`;
  return runPostgresJson(sql);
}

async function seedDatasetOptimizationFixture({
  runId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const optimizationId = randomUUID();
  const stepId = randomUUID();
  const baselineTrialId = randomUUID();
  const trialId = randomUUID();
  const baselineItemId = randomUUID();
  const trialItemId = randomUUID();
  const baselineEvaluationId = randomUUID();
  const trialEvaluationId = randomUUID();
  const optimizationName = `api_journey_dataset_opt_${runId}`;
  const workspacePredicate = workspaceId
    ? `(d.workspace_id = ${sqlUuid(workspaceId)} OR d.workspace_id IS NULL)`
    : "true";
  const metricWorkspacePredicate = workspaceId
    ? `(m.workspace_id = ${sqlUuid(workspaceId)} OR m.workspace_id IS NULL)`
    : "true";
  const sql = `
WITH candidate AS (
  SELECT
    c.id AS column_id,
    d.id AS dataset_id,
    m.id AS metric_id
  FROM model_hub_dataset d
  JOIN model_hub_column c ON c.dataset_id = d.id AND c.deleted = false
  JOIN model_hub_userevalmetric m
    ON m.dataset_id = d.id
   AND m.organization_id = d.organization_id
   AND m.deleted = false
   AND ${metricWorkspacePredicate}
  WHERE d.organization_id = ${sqlUuid(organizationId)}
    AND d.deleted = false
    AND ${workspacePredicate}
  ORDER BY
    CASE WHEN d.workspace_id = ${workspaceId ? sqlUuid(workspaceId) : "NULL::uuid"} THEN 0 ELSE 1 END,
    d.created_at DESC,
    c.created_at DESC
  LIMIT 1
),
inserted_run AS (
  INSERT INTO model_hub_optimizedataset (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    name,
    environment,
    version,
    status,
    optimized_k_prompts,
    optimize_type,
    eval_instructions,
    criteria_breakdown,
    used_in,
    column_id,
    optimizer_algorithm,
    optimizer_config,
    best_score,
    baseline_score
  )
  SELECT
    ${sqlUuid(optimizationId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    ${sqlTextLiteral(optimizationName)},
    'Training',
    '1.0',
    'completed',
    ARRAY['api journey optimized prompt']::text[],
    'PromptTemplate',
    '{}'::jsonb,
    ARRAY[]::varchar[],
    'develop',
    candidate.column_id,
    'random_search',
    jsonb_build_object('num_variations', 1, 'model_name', 'gpt-4o-mini'),
    0.82,
    0.41
  FROM candidate
  RETURNING id
),
inserted_metric_link AS (
  INSERT INTO model_hub_optimizedataset_user_eval_template_ids (
    optimizedataset_id,
    userevalmetric_id
  )
  SELECT inserted_run.id, candidate.metric_id
  FROM inserted_run, candidate
  RETURNING 1
),
inserted_steps AS (
  INSERT INTO dataset_optimization_step (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    optimization_run_id,
    step_number,
    name,
    description,
    status,
    metadata
  )
  SELECT
    ${sqlUuid(stepId)},
    now(),
    now(),
    false,
    NULL::timestamptz,
    inserted_run.id,
    1,
    'Seeded optimization step',
    'API journey seeded step',
    'completed',
    '{}'::jsonb
  FROM inserted_run
  RETURNING 1
),
inserted_trials AS (
  INSERT INTO dataset_optimization_trial (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    optimization_run_id,
    trial_number,
    is_baseline,
    prompt,
    average_score,
    metadata
  )
  SELECT
    ${sqlUuid(baselineTrialId)}, now(), now(), false, NULL::timestamptz, inserted_run.id,
    0, true, 'api journey baseline prompt', 0.41,
    jsonb_build_object('individual_results', jsonb_build_array())
  FROM inserted_run
  UNION ALL
  SELECT
    ${sqlUuid(trialId)}, now(), now(), false, NULL::timestamptz, inserted_run.id,
    1, false, 'api journey trial prompt', 0.82,
    jsonb_build_object('individual_results', jsonb_build_array())
  FROM inserted_run
  RETURNING id
),
inserted_items AS (
  INSERT INTO dataset_optimization_trial_item (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    trial_id,
    row_id,
    score,
    reason,
    input_text,
    output_text,
    filled_prompt,
    metadata
  )
  VALUES
    (
      ${sqlUuid(baselineItemId)}, now(), now(), false, NULL::timestamptz,
      ${sqlUuid(baselineTrialId)}, 'row-1', 0.41, 'baseline reason',
      '{"input":"seed"}', 'baseline output', 'baseline filled prompt', '{}'::jsonb
    ),
    (
      ${sqlUuid(trialItemId)}, now(), now(), false, NULL::timestamptz,
      ${sqlUuid(trialId)}, 'row-1', 0.82, 'trial reason',
      '{"input":"seed"}', 'trial output', 'trial filled prompt', '{}'::jsonb
    )
  RETURNING id
),
inserted_evaluations AS (
  INSERT INTO dataset_optimization_item_evaluation (
    id,
    created_at,
    updated_at,
    deleted,
    deleted_at,
    trial_item_id,
    eval_metric_id,
    score,
    reason
  )
  SELECT
    ${sqlUuid(baselineEvaluationId)}, now(), now(), false, NULL::timestamptz,
    ${sqlUuid(baselineItemId)}, candidate.metric_id, 0.41, 'baseline eval'
  FROM candidate
  UNION ALL
  SELECT
    ${sqlUuid(trialEvaluationId)}, now(), now(), false, NULL::timestamptz,
    ${sqlUuid(trialItemId)}, candidate.metric_id, 0.82, 'trial eval'
  FROM candidate
  RETURNING 1
)
SELECT json_build_object(
  'fixture_created', EXISTS(SELECT 1 FROM inserted_run),
  'optimization_id', ${sqlTextLiteral(optimizationId)},
  'optimization_name', ${sqlTextLiteral(optimizationName)},
  'dataset_id', (SELECT dataset_id::text FROM candidate),
  'column_id', (SELECT column_id::text FROM candidate),
  'metric_id', (SELECT metric_id::text FROM candidate),
  'trial_id', ${sqlTextLiteral(trialId)}
);
`;
  return runPostgresJson(sql);
}

async function loadDatasetOptimizationDeleteAudit(optimizationId) {
  assert(isUuid(optimizationId), "optimizationId must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'optimization_id', o.id::text,
  'run_deleted', o.deleted,
  'run_deleted_at_set', o.deleted_at IS NOT NULL,
  'active_step_count', (
    SELECT count(*) FROM dataset_optimization_step s
    WHERE s.optimization_run_id = o.id AND s.deleted = false
  ),
  'active_trial_count', (
    SELECT count(*) FROM dataset_optimization_trial t
    WHERE t.optimization_run_id = o.id AND t.deleted = false
  ),
  'active_item_count', (
    SELECT count(*)
    FROM dataset_optimization_trial_item item
    JOIN dataset_optimization_trial t ON t.id = item.trial_id
    WHERE t.optimization_run_id = o.id AND item.deleted = false
  ),
  'active_evaluation_count', (
    SELECT count(*)
    FROM dataset_optimization_item_evaluation eval
    JOIN dataset_optimization_trial_item item ON item.id = eval.trial_item_id
    JOIN dataset_optimization_trial t ON t.id = item.trial_id
    WHERE t.optimization_run_id = o.id AND eval.deleted = false
  ),
  'deleted_step_deleted_at_count', (
    SELECT count(*) FROM dataset_optimization_step s
    WHERE s.optimization_run_id = o.id
      AND s.deleted = true
      AND s.deleted_at IS NOT NULL
  ),
  'deleted_trial_deleted_at_count', (
    SELECT count(*) FROM dataset_optimization_trial t
    WHERE t.optimization_run_id = o.id
      AND t.deleted = true
      AND t.deleted_at IS NOT NULL
  ),
  'deleted_item_deleted_at_count', (
    SELECT count(*)
    FROM dataset_optimization_trial_item item
    JOIN dataset_optimization_trial t ON t.id = item.trial_id
    WHERE t.optimization_run_id = o.id
      AND item.deleted = true
      AND item.deleted_at IS NOT NULL
  ),
  'deleted_evaluation_deleted_at_count', (
    SELECT count(*)
    FROM dataset_optimization_item_evaluation eval
    JOIN dataset_optimization_trial_item item ON item.id = eval.trial_item_id
    JOIN dataset_optimization_trial t ON t.id = item.trial_id
    WHERE t.optimization_run_id = o.id
      AND eval.deleted = true
      AND eval.deleted_at IS NOT NULL
  )
)
FROM model_hub_optimizedataset o
WHERE o.id = ${sqlUuid(optimizationId)};
`;
  return runPostgresJson(sql);
}

async function hardDeleteDatasetOptimizationFixture(optimizationId) {
  assert(
    isUuid(optimizationId),
    "optimizationId must be a UUID for DB cleanup.",
  );
  const sql = `
WITH deleted_evaluations AS (
  DELETE FROM dataset_optimization_item_evaluation eval
  USING dataset_optimization_trial_item item, dataset_optimization_trial trial
  WHERE eval.trial_item_id = item.id
    AND item.trial_id = trial.id
    AND trial.optimization_run_id = ${sqlUuid(optimizationId)}
  RETURNING 1
),
deleted_items AS (
  DELETE FROM dataset_optimization_trial_item item
  USING dataset_optimization_trial trial
  WHERE item.trial_id = trial.id
    AND trial.optimization_run_id = ${sqlUuid(optimizationId)}
  RETURNING 1
),
deleted_trials AS (
  DELETE FROM dataset_optimization_trial
  WHERE optimization_run_id = ${sqlUuid(optimizationId)}
  RETURNING 1
),
deleted_steps AS (
  DELETE FROM dataset_optimization_step
  WHERE optimization_run_id = ${sqlUuid(optimizationId)}
  RETURNING 1
),
deleted_metric_links AS (
  DELETE FROM model_hub_optimizedataset_user_eval_template_ids
  WHERE optimizedataset_id = ${sqlUuid(optimizationId)}
  RETURNING 1
),
deleted_run AS (
  DELETE FROM model_hub_optimizedataset
  WHERE id = ${sqlUuid(optimizationId)}
  RETURNING 1
)
SELECT json_build_object(
  'deleted_run_count', (SELECT count(*) FROM deleted_run),
  'deleted_step_count', (SELECT count(*) FROM deleted_steps),
  'deleted_trial_count', (SELECT count(*) FROM deleted_trials),
  'deleted_item_count', (SELECT count(*) FROM deleted_items),
  'deleted_evaluation_count', (SELECT count(*) FROM deleted_evaluations),
  'deleted_metric_link_count', (SELECT count(*) FROM deleted_metric_links)
);
`;
  return runPostgresJson(sql);
}

async function loadUserSdkCredentialDbAudit(organizationId, email) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  assert(email, "email is required for SDK credential DB audit.");
  const sql = `
SELECT json_build_object(
  'user_key_count', count(*)::int,
  'enabled_user_key_count', COALESCE(count(*) FILTER (
    WHERE ak.enabled = true AND ak.deleted = false
  ), 0)::int
)
FROM accounts_orgapikey ak
JOIN accounts_user u ON u.id = ak.user_id
WHERE ak.organization_id = ${sqlUuid(organizationId)}
  AND ak.type = 'user'
  AND ak.deleted = false
  AND u.email = ${sqlTextLiteral(email)};
`;
  return runPostgresJson(sql);
}

async function seedAnnotationTaskFixture({
  runId,
  userId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(userId), "userId must be a UUID for AnnotationTask seed.");
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for AnnotationTask seed.",
  );
  assert(
    isUuid(workspaceId),
    "workspaceId must be a UUID for AnnotationTask seed.",
  );
  const aiModelId = randomUUID();
  const annotationTaskId = randomUUID();
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const modelUserId = `api_journey_annotation_task_model_${suffix}`;
  const taskName = `api journey annotation task ${runId}`;
  const sql = `
WITH inserted_model AS (
  INSERT INTO model_hub_aimodel (
    id,
    organization_id,
    workspace_id,
    created_at,
    model_type,
    user_model_id,
    deleted
  )
  VALUES (
    ${sqlUuid(aiModelId)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)},
    NOW(),
    'GenerativeLLM',
    ${sqlTextLiteral(modelUserId)},
    false
  )
  RETURNING id, user_model_id
),
inserted_task AS (
  INSERT INTO model_hub_annotationtask (
    id,
    created_at,
    updated_at,
    is_deleted,
    ai_model_id,
    task_name,
    organization_id,
    workspace_id
  )
  SELECT
    ${sqlUuid(annotationTaskId)},
    NOW(),
    NOW(),
    false,
    id,
    ${sqlTextLiteral(taskName)},
    ${sqlUuid(organizationId)},
    ${sqlUuid(workspaceId)}
  FROM inserted_model
  RETURNING id
),
inserted_assignment AS (
  INSERT INTO model_hub_annotationtask_assigned_users (
    annotationtask_id,
    user_id
  )
  SELECT id, ${sqlUuid(userId)}
  FROM inserted_task
  RETURNING id
)
SELECT json_build_object(
  'fixture_created', EXISTS(SELECT 1 FROM inserted_model)
    AND EXISTS(SELECT 1 FROM inserted_task)
    AND EXISTS(SELECT 1 FROM inserted_assignment),
  'ai_model_id', ${sqlTextLiteral(aiModelId)},
  'annotation_task_id', ${sqlTextLiteral(annotationTaskId)},
  'task_name', ${sqlTextLiteral(taskName)},
  'model_user_id', ${sqlTextLiteral(modelUserId)},
  'organization_id', ${sqlTextLiteral(organizationId)},
  'workspace_id', ${sqlTextLiteral(workspaceId)},
  'assigned_user_id', ${sqlTextLiteral(userId)}
);
`;
  return runPostgresJson(sql);
}

async function hardDeleteAnnotationTaskFixture(fixture) {
  assert(
    isUuid(fixture?.annotation_task_id) && isUuid(fixture?.ai_model_id),
    "AnnotationTask fixture cleanup requires task and model ids.",
  );
  const sql = `
WITH deleted_assignments AS (
  DELETE FROM model_hub_annotationtask_assigned_users
  WHERE annotationtask_id = ${sqlUuid(fixture.annotation_task_id)}
  RETURNING 1
),
deleted_clickhouse_annotations AS (
  DELETE FROM model_hub_clickhouseannotation
  WHERE annotation_task_id = ${sqlUuid(fixture.annotation_task_id)}
  RETURNING 1
),
deleted_task AS (
  DELETE FROM model_hub_annotationtask
  WHERE id = ${sqlUuid(fixture.annotation_task_id)}
  RETURNING 1
),
deleted_model AS (
  DELETE FROM model_hub_aimodel
  WHERE id = ${sqlUuid(fixture.ai_model_id)}
  RETURNING 1
)
SELECT json_build_object(
  'deleted_assignment_count', (SELECT COUNT(*)::int FROM deleted_assignments),
  'deleted_clickhouse_annotation_count',
    (SELECT COUNT(*)::int FROM deleted_clickhouse_annotations),
  'deleted_task_count', (SELECT COUNT(*)::int FROM deleted_task),
  'deleted_ai_model_count', (SELECT COUNT(*)::int FROM deleted_model)
);
`;
  const deleteAudit = await runPostgresJson(sql);
  const residueAudit = await loadAnnotationTaskFixtureResidue(fixture);
  return { ...deleteAudit, ...residueAudit };
}

async function loadAnnotationTaskFixtureResidue(fixture) {
  assert(
    isUuid(fixture?.annotation_task_id) && isUuid(fixture?.ai_model_id),
    "AnnotationTask fixture residue check requires task and model ids.",
  );
  const sql = `
SELECT json_build_object(
  'remaining_assignment_count', (
    SELECT COUNT(*)::int
    FROM model_hub_annotationtask_assigned_users
    WHERE annotationtask_id = ${sqlUuid(fixture.annotation_task_id)}
  ),
  'remaining_task_count', (
    SELECT COUNT(*)::int
    FROM model_hub_annotationtask
    WHERE id = ${sqlUuid(fixture.annotation_task_id)}
  ),
  'remaining_ai_model_count', (
    SELECT COUNT(*)::int
    FROM model_hub_aimodel
    WHERE id = ${sqlUuid(fixture.ai_model_id)}
  )
);
`;
  return runPostgresJson(sql);
}

function assertAnnotationTaskPayload(task, fixture, userId) {
  assert(
    task?.id === fixture.annotation_task_id,
    "AnnotationTask payload returned the wrong task id.",
  );
  assert(
    task.task_name === fixture.task_name,
    "AnnotationTask payload returned the wrong task name.",
  );
  assert(
    task.ai_model?.id === fixture.ai_model_id,
    "AnnotationTask payload returned the wrong AI model.",
  );
  assert(
    asArray(task.assigned_users).some((assigned) => assigned.id === userId),
    "AnnotationTask payload did not include the assigned user.",
  );
}

async function loadLegacyAnnotationLifecycleDbAudit({
  annotationIds,
  datasetId,
  labelId,
  organizationId,
  workspaceId,
}) {
  assert(
    Array.isArray(annotationIds) && annotationIds.length > 0,
    "annotationIds are required for legacy annotation DB audit.",
  );
  for (const annotationId of annotationIds) {
    assert(isUuid(annotationId), "annotationId must be a UUID for DB audit.");
  }
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(labelId), "labelId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId) {
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  }
  const workspacePredicate = workspaceId
    ? `AND a.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH target AS (
  SELECT unnest(${sqlUuidArray(annotationIds)})::uuid AS id
),
annotation_rows AS (
  SELECT a.*
  FROM model_hub_annotations a
  JOIN target t ON t.id = a.id
  WHERE a.dataset_id = ${sqlUuid(datasetId)}
    AND a.organization_id = ${sqlUuid(organizationId)}
    ${workspacePredicate}
),
generated_columns AS (
  SELECT c.*
  FROM model_hub_column c
  JOIN target t ON c.source_id LIKE (t.id::text || '-sourceid-%')
  WHERE c.dataset_id = ${sqlUuid(datasetId)}
),
generated_cells AS (
  SELECT cell.*
  FROM model_hub_cell cell
  JOIN generated_columns c ON c.id = cell.column_id
  WHERE cell.dataset_id = ${sqlUuid(datasetId)}
),
label_binding AS (
  SELECT COUNT(*)::int AS binding_count
  FROM model_hub_annotations_labels al
  JOIN target t ON t.id = al.annotations_id
  WHERE al.annotationslabels_id = ${sqlUuid(labelId)}
)
SELECT json_build_object(
  'annotation_count', (SELECT COUNT(*)::int FROM annotation_rows),
  'active_annotation_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = false)::int FROM annotation_rows
  ), 0),
  'deleted_annotation_with_deleted_at_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = true AND deleted_at IS NOT NULL)::int
    FROM annotation_rows
  ), 0),
  'label_binding_count', COALESCE((SELECT binding_count FROM label_binding), 0),
  'generated_column_count', (SELECT COUNT(*)::int FROM generated_columns),
  'active_generated_column_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = false)::int FROM generated_columns
  ), 0),
  'deleted_generated_column_deleted_at_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = true AND deleted_at IS NOT NULL)::int
    FROM generated_columns
  ), 0),
  'generated_cell_count', (SELECT COUNT(*)::int FROM generated_cells),
  'active_generated_cell_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = false)::int FROM generated_cells
  ), 0),
  'deleted_generated_cell_deleted_at_count', COALESCE((
    SELECT COUNT(*) FILTER (WHERE deleted = true AND deleted_at IS NOT NULL)::int
    FROM generated_cells
  ), 0)
);
`;
  return runPostgresJson(sql);
}

async function loadKnowledgeBaseLegacyDbAudit({
  kbId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(kbId), "kbId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH kb AS (
  SELECT id, name, organization_id, workspace_id, deleted, deleted_at
  FROM model_hub_knowledgebasefile
  WHERE id = ${sqlUuid(kbId)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'kb_id', kb.id::text,
  'name', kb.name,
  'organization_id', kb.organization_id::text,
  'workspace_id', kb.workspace_id::text,
  'deleted', kb.deleted,
  'deleted_at_set', kb.deleted_at IS NOT NULL,
  'file_count', COALESCE(count(f.id), 0)::int,
  'file_ids', COALESCE(
    json_agg(f.id::text ORDER BY f.name) FILTER (WHERE f.id IS NOT NULL),
    '[]'::json
  ),
  'file_statuses', COALESCE(
    json_object_agg(f.id::text, f.status) FILTER (WHERE f.id IS NOT NULL),
    '{}'::json
  )
)
FROM kb
LEFT JOIN model_hub_knowledgebasefile_files kbf
  ON kbf.knowledgebasefile_id = kb.id
LEFT JOIN model_hub_files f
  ON f.id = kbf.files_id
GROUP BY kb.id, kb.name, kb.organization_id, kb.workspace_id, kb.deleted, kb.deleted_at;
`;
  return runPostgresJson(sql);
}

async function loadKnowledgeBaseOutsideFileCandidate({ kbId, organizationId }) {
  assert(isUuid(kbId), "kbId must be a UUID for outside file audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for audit.");
  const sql = `
SELECT COALESCE((
  SELECT json_build_object(
    'outside_kb_id', other_kb.id::text,
    'outside_file_id', f.id::text,
    'outside_file_name', f.name,
    'outside_file_status', f.status
  )
  FROM model_hub_knowledgebasefile other_kb
  JOIN model_hub_knowledgebasefile_files kbf
    ON kbf.knowledgebasefile_id = other_kb.id
  JOIN model_hub_files f
    ON f.id = kbf.files_id
  WHERE other_kb.organization_id = ${sqlUuid(organizationId)}
    AND other_kb.id <> ${sqlUuid(kbId)}
    AND other_kb.deleted = false
    AND f.deleted = false
  ORDER BY other_kb.created_at DESC, f.created_at DESC
  LIMIT 1
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadKnowledgeBaseFileDbAudit(fileId) {
  assert(isUuid(fileId), "fileId must be a UUID for file DB audit.");
  const sql = `
SELECT json_build_object(
  'file_id', f.id::text,
  'name', f.name,
  'status', f.status,
  'deleted', f.deleted,
  'deleted_at_set', f.deleted_at IS NOT NULL
)
FROM model_hub_files f
WHERE f.id = ${sqlUuid(fileId)};
`;
  return runPostgresJson(sql);
}

async function loadStructuredKnowledgeBaseDbAudit({
  kbId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(kbId), "kbId must be a UUID for structured KB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'kb_id', kb.id::text,
  'name', kb.name,
  'embedding_model', kb.embedding_model,
  'chunk_size', kb.chunk_size,
  'organization_id', kb.organization_id::text,
  'workspace_id', kb.workspace_id::text,
  'deleted', kb.deleted,
  'deleted_at_set', kb.deleted_at IS NOT NULL
)
FROM model_hub_knowledgebase kb
WHERE kb.id = ${sqlUuid(kbId)}
  AND kb.organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

async function loadDatasetRowColumnLifecycleDbAudit(
  datasetId,
  rowIds,
  columnIds,
  organizationId,
  workspaceId,
) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  assert(rowIds.length > 0, "rowIds must not be empty for DB audit.");
  assert(columnIds.length > 0, "columnIds must not be empty for DB audit.");
  const rowArray = sqlUuidArray(rowIds);
  const columnArray = sqlUuidArray(columnIds);
  const columnTextArray = sqlTextArray(columnIds);
  const sql = `
WITH dataset_row AS (
  SELECT id, name, organization_id, workspace_id, column_order, column_config
  FROM model_hub_dataset
  WHERE id = ${sqlUuid(datasetId)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
)
SELECT json_build_object(
  'dataset_id', d.id::text,
  'dataset_name', d.name,
  'organization_id', d.organization_id::text,
  'workspace_id', d.workspace_id::text,
  'active_temp_rows', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.id = ANY(${rowArray}) AND r.dataset_id = d.id AND r.deleted = false
  ),
  'deleted_temp_rows', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.id = ANY(${rowArray}) AND r.dataset_id = d.id AND r.deleted = true
  ),
  'deleted_row_deleted_at_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.id = ANY(${rowArray})
      AND r.dataset_id = d.id
      AND r.deleted = true
      AND r.deleted_at IS NOT NULL
  ),
  'active_temp_columns', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.id = ANY(${columnArray}) AND c.dataset_id = d.id AND c.deleted = false
  ),
  'deleted_temp_columns', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.id = ANY(${columnArray}) AND c.dataset_id = d.id AND c.deleted = true
  ),
  'deleted_column_deleted_at_count', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.id = ANY(${columnArray})
      AND c.dataset_id = d.id
      AND c.deleted = true
      AND c.deleted_at IS NOT NULL
  ),
  'active_temp_cells', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND (cell.row_id = ANY(${rowArray}) OR cell.column_id = ANY(${columnArray}))
      AND cell.deleted = false
  ),
  'deleted_temp_cells', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND (cell.row_id = ANY(${rowArray}) OR cell.column_id = ANY(${columnArray}))
      AND cell.deleted = true
  ),
  'deleted_cell_deleted_at_count', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND (cell.row_id = ANY(${rowArray}) OR cell.column_id = ANY(${columnArray}))
      AND cell.deleted = true
      AND cell.deleted_at IS NOT NULL
  ),
  'column_order_contains_deleted_count', (
    SELECT count(*)
    FROM unnest(COALESCE(d.column_order, ARRAY[]::varchar[])) AS column_id
    WHERE column_id = ANY(${columnTextArray})
  ),
  'column_config_contains_deleted_count', (
    SELECT count(*)
    FROM jsonb_object_keys(COALESCE(d.column_config::jsonb, '{}'::jsonb)) AS column_id
    WHERE column_id = ANY(${columnTextArray})
  )
)
FROM dataset_row d;
`;
  return runPostgresJson(sql);
}

async function loadRunPromptColumnDbAudit({
  datasetId,
  rowId,
  columnId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(rowId), "rowId must be a UUID for DB audit.");
  assert(isUuid(columnId), "columnId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH selected_column AS (
  SELECT c.id, c.name, c.dataset_id, c.source_id, c.deleted, c.deleted_at
  FROM model_hub_column c
  WHERE c.id = ${sqlUuid(columnId)}
),
selected_run_prompt AS (
  SELECT rp.*
  FROM model_hub_runprompter rp
  JOIN selected_column c ON NULLIF(c.source_id, '')::uuid = rp.id
),
selected_dataset AS (
  SELECT d.id, d.name, d.organization_id, d.workspace_id
  FROM model_hub_dataset d
  WHERE d.id = ${sqlUuid(datasetId)}
)
SELECT json_build_object(
  'dataset_id', d.id::text,
  'dataset_name', d.name,
  'organization_id', d.organization_id::text,
  'workspace_id', d.workspace_id::text,
  'column_id', c.id::text,
  'column_name', c.name,
  'column_deleted', c.deleted,
  'column_deleted_at_set', c.deleted_at IS NOT NULL,
  'run_prompt_id', rp.id::text,
  'run_prompt_name', rp.name,
  'run_prompt_model', rp.model,
  'run_prompt_status', rp.status,
  'run_prompt_dataset_id', rp.dataset_id::text,
  'run_prompt_organization_id', rp.organization_id::text,
  'run_prompt_workspace_id', rp.workspace_id::text,
  'run_prompt_deleted', rp.deleted,
  'run_prompt_deleted_at_set', rp.deleted_at IS NOT NULL,
  'active_cell_count', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND cell.row_id = ${sqlUuid(rowId)}
      AND cell.column_id = c.id
      AND cell.deleted = false
  ),
  'deleted_cell_count', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND cell.row_id = ${sqlUuid(rowId)}
      AND cell.column_id = c.id
      AND cell.deleted = true
  ),
  'deleted_cell_deleted_at_count', (
    SELECT count(*)
    FROM model_hub_cell cell
    WHERE cell.dataset_id = d.id
      AND cell.row_id = ${sqlUuid(rowId)}
      AND cell.column_id = c.id
      AND cell.deleted = true
      AND cell.deleted_at IS NOT NULL
  )
)
FROM selected_dataset d
JOIN selected_column c ON c.dataset_id = d.id
JOIN selected_run_prompt rp ON rp.dataset_id = d.id;
`;
  return runPostgresJson(sql);
}

async function loadExperimentReadDbAudit(
  experimentId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(experimentId), "experimentId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH experiment_row AS (
  SELECT
    e.id,
    e.name,
    e.status,
    e.experiment_type,
    e.dataset_id,
    e.snapshot_dataset_id,
    e.column_id,
    d.organization_id,
    d.workspace_id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  WHERE e.id = ${sqlUuid(experimentId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
    AND e.deleted = false
    AND d.deleted = false
)
SELECT json_build_object(
  'experiment_id', e.id::text,
  'experiment_name', e.name,
  'status', e.status,
  'experiment_type', e.experiment_type,
  'dataset_id', e.dataset_id::text,
  'snapshot_dataset_id', e.snapshot_dataset_id::text,
  'column_id', e.column_id::text,
  'organization_id', e.organization_id::text,
  'workspace_id', e.workspace_id::text,
  'source_row_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.dataset_id = e.dataset_id AND r.deleted = false
  ),
  'snapshot_row_count', (
    SELECT count(*)
    FROM model_hub_row r
    WHERE r.dataset_id = e.snapshot_dataset_id AND r.deleted = false
  ),
  'snapshot_column_count', (
    SELECT count(*)
    FROM model_hub_column c
    WHERE c.dataset_id = e.snapshot_dataset_id AND c.deleted = false
  ),
  'fk_experiment_dataset_count', (
    SELECT count(*)
    FROM model_hub_experimentdatasettable edt
    WHERE edt.experiment_id = e.id AND edt.deleted = false
  ),
  'legacy_experiment_dataset_count', (
    SELECT count(*)
    FROM model_hub_experimentstable_experiments_datasets m
    JOIN model_hub_experimentdatasettable edt
      ON edt.id = m.experimentdatasettable_id
    WHERE m.experimentstable_id = e.id AND edt.deleted = false
  ),
  'experiment_dataset_column_count', (
    SELECT count(DISTINCT edtc.column_id)
    FROM model_hub_experimentdatasettable edt
    JOIN model_hub_experimentdatasettable_columns edtc
      ON edtc.experimentdatasettable_id = edt.id
    WHERE edt.experiment_id = e.id AND edt.deleted = false
  ),
  'prompt_config_count', (
    SELECT count(*)
    FROM model_hub_experimentpromptconfig epc
    JOIN model_hub_experimentdatasettable edt
      ON edt.id = epc.experiment_dataset_id
    WHERE edt.experiment_id = e.id
      AND edt.deleted = false
      AND epc.deleted = false
  ),
  'agent_config_count', (
    SELECT count(*)
    FROM model_hub_experimentagentconfig eac
    JOIN model_hub_experimentdatasettable edt
      ON edt.id = eac.experiment_dataset_id
    WHERE edt.experiment_id = e.id
      AND edt.deleted = false
      AND eac.deleted = false
  ),
  'eval_metric_count', (
    SELECT count(*)
    FROM model_hub_experimentstable_user_eval_template_ids m
    JOIN model_hub_userevalmetric uem
      ON uem.id = m.userevalmetric_id
    WHERE m.experimentstable_id = e.id AND uem.deleted = false
  ),
  'eval_metric_ids', (
    SELECT COALESCE(json_agg(uem.id::text ORDER BY uem.created_at), '[]'::json)
    FROM model_hub_experimentstable_user_eval_template_ids m
    JOIN model_hub_userevalmetric uem
      ON uem.id = m.userevalmetric_id
    WHERE m.experimentstable_id = e.id AND uem.deleted = false
  ),
  'comparison_count', (
    SELECT count(*)
    FROM model_hub_experimentcomparison ec
    WHERE ec.experiment_id = e.id AND ec.deleted = false
  )
)
FROM experiment_row e;
`;
  return runPostgresJson(sql);
}

async function loadExperimentActionGuardCandidate(
  experimentId,
  organizationId,
  workspaceId,
) {
  assert(
    isUuid(experimentId),
    "experimentId must be a UUID for action guard candidate.",
  );
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for action guard candidate.",
  );
  if (workspaceId) {
    assert(
      isUuid(workspaceId),
      "workspaceId must be a UUID for action guard candidate.",
    );
  }
  const workspaceFilter = workspaceId
    ? `AND d.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH experiment_row AS (
  SELECT
    e.id,
    e.status,
    e.dataset_id,
    e.snapshot_dataset_id,
    e.column_id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  WHERE e.id = ${sqlUuid(experimentId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
    AND e.deleted = false
    AND d.deleted = false
),
candidate AS (
  SELECT
    e.*,
    (
      SELECT r.id
      FROM model_hub_row r
      WHERE r.dataset_id = e.snapshot_dataset_id
        AND r.deleted = false
      ORDER BY r.created_at
      LIMIT 1
    ) AS snapshot_row_id,
    COALESCE(
      (
        SELECT c.id
        FROM model_hub_column c
        WHERE c.id = e.column_id
          AND c.dataset_id = e.snapshot_dataset_id
          AND c.deleted = false
        LIMIT 1
      ),
      (
        SELECT c.id
        FROM model_hub_column c
        WHERE c.dataset_id = e.snapshot_dataset_id
          AND c.source = 'experiment'
          AND c.deleted = false
        ORDER BY c.created_at
        LIMIT 1
      ),
      (
        SELECT c.id
        FROM model_hub_column c
        WHERE c.dataset_id = e.snapshot_dataset_id
          AND c.deleted = false
        ORDER BY c.created_at
        LIMIT 1
      )
    ) AS snapshot_column_id
  FROM experiment_row e
)
SELECT json_build_object(
  'experiment_id', c.id::text,
  'status', c.status,
  'dataset_id', c.dataset_id::text,
  'snapshot_dataset_id', c.snapshot_dataset_id::text,
  'snapshot_row_id', c.snapshot_row_id::text,
  'snapshot_column_id', c.snapshot_column_id::text,
  'snapshot_column_status', col.status
)
FROM candidate c
LEFT JOIN model_hub_column col ON col.id = c.snapshot_column_id;
`;
  return runPostgresJson(sql);
}

async function loadColumnStatus(columnId) {
  assert(isUuid(columnId), "columnId must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'column_id', c.id::text,
  'status', c.status,
  'deleted', c.deleted
)
FROM model_hub_column c
WHERE c.id = ${sqlUuid(columnId)};
`;
  return runPostgresJson(sql);
}

async function loadExperimentFeedbackCandidate(
  experimentId,
  organizationId,
  workspaceId,
) {
  assert(
    isUuid(experimentId),
    "experimentId must be a UUID for feedback candidate.",
  );
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for feedback candidate.",
  );
  if (workspaceId) {
    assert(
      isUuid(workspaceId),
      "workspaceId must be a UUID for feedback candidate.",
    );
  }
  const workspaceFilter = workspaceId
    ? `AND d.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH experiment_row AS (
  SELECT
    e.id,
    e.name,
    e.snapshot_dataset_id,
    d.organization_id,
    d.workspace_id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  WHERE e.id = ${sqlUuid(experimentId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
    AND e.deleted = false
    AND d.deleted = false
),
metric AS (
  SELECT
    uem.id,
    uem.name,
    et.config
  FROM model_hub_experimentstable_user_eval_template_ids m
  JOIN model_hub_userevalmetric uem
    ON uem.id = m.userevalmetric_id
  LEFT JOIN model_hub_evaltemplate et
    ON et.id = uem.template_id
  JOIN experiment_row e
    ON e.id = m.experimentstable_id
  WHERE uem.deleted = false
    AND uem.organization_id = e.organization_id
  ORDER BY
    CASE WHEN et.config ->> 'output' = 'Pass/Fail' THEN 0 ELSE 1 END,
    uem.created_at
),
eval_column AS (
  SELECT
    c.id,
    c.name,
    c.source_id,
    m.id AS user_eval_metric_id,
    m.config
  FROM model_hub_column c
  JOIN experiment_row e
    ON c.dataset_id = e.snapshot_dataset_id
  JOIN metric m
    ON (
      c.source_id = m.id::text
      OR c.source_id LIKE ('%-sourceid-' || m.id::text)
    )
  WHERE c.deleted = false
    AND c.source IN ('experiment_evaluation', 'evaluation')
  ORDER BY
    CASE WHEN c.name ILIKE '%reason%' THEN 1 ELSE 0 END,
    c.created_at
  LIMIT 1
),
snapshot_row AS (
  SELECT r.id
  FROM model_hub_row r
  JOIN experiment_row e
    ON r.dataset_id = e.snapshot_dataset_id
  WHERE r.deleted = false
  ORDER BY r."order", r.created_at
  LIMIT 1
)
SELECT COALESCE((
  SELECT json_build_object(
    'experiment_id', e.id::text,
    'experiment_name', e.name,
    'snapshot_dataset_id', e.snapshot_dataset_id::text,
    'organization_id', e.organization_id::text,
    'workspace_id', e.workspace_id::text,
    'user_eval_metric_id', ec.user_eval_metric_id::text,
    'eval_column_id', ec.id::text,
    'eval_column_name', ec.name,
    'row_id', (SELECT id::text FROM snapshot_row),
    'feedback_value', 'Failed'
  )
  FROM experiment_row e
  LEFT JOIN eval_column ec ON true
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadExperimentFeedbackDbAudit({
  feedbackId,
  experimentId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(feedbackId), "feedbackId must be a UUID for DB audit.");
  assert(isUuid(experimentId), "experimentId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId) {
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  }
  const workspaceFilter = workspaceId
    ? `AND d.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH experiment_row AS (
  SELECT
    e.id,
    e.snapshot_dataset_id,
    d.organization_id,
    d.workspace_id
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d ON d.id = e.dataset_id
  WHERE e.id = ${sqlUuid(experimentId)}
    AND d.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
    AND e.deleted = false
    AND d.deleted = false
),
feedback_row AS (
  SELECT *
  FROM model_hub_feedback
  WHERE id = ${sqlUuid(feedbackId)}
),
source_column AS (
  SELECT c.*
  FROM model_hub_column c
  JOIN feedback_row f
    ON c.id::text = f.source_id
),
feedback_snapshot_row AS (
  SELECT r.*
  FROM model_hub_row r
  JOIN feedback_row f
    ON r.id::text = f.row_id
)
SELECT COALESCE((
  SELECT json_build_object(
    'feedback_id', f.id::text,
    'experiment_id', e.id::text,
    'organization_id', f.organization_id::text,
    'workspace_id', f.workspace_id::text,
    'feedback_source', f.source,
    'feedback_source_id', f.source_id,
    'feedback_row_id', f.row_id,
    'user_eval_metric_id', f.user_eval_metric_id::text,
    'eval_template_id', f.eval_template_id::text,
    'value', f.value,
    'explanation', f.explanation,
    'action_type', f.action_type,
    'feedback_deleted', f.deleted,
    'feedback_deleted_at_set', f.deleted_at IS NOT NULL,
    'source_column_in_snapshot', sc.dataset_id = e.snapshot_dataset_id,
    'source_column_matches_metric',
      sc.source_id = f.user_eval_metric_id::text
      OR sc.source_id LIKE ('%-sourceid-' || f.user_eval_metric_id::text),
    'row_in_snapshot', fsr.dataset_id = e.snapshot_dataset_id
  )
  FROM experiment_row e
  JOIN feedback_row f ON true
  LEFT JOIN source_column sc ON true
  LEFT JOIN feedback_snapshot_row fsr ON true
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function loadEvalTemplateLifecycleDbAudit(
  templateId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH template_row AS (
  SELECT
    id,
    name,
    owner,
    organization_id,
    workspace_id,
    template_type,
    eval_type,
    output_type_normalized,
    config,
    deleted,
    deleted_at
  FROM model_hub_evaltemplate
  WHERE id = ${sqlUuid(templateId)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'template_id', t.id::text,
  'name', t.name,
  'owner', t.owner,
  'organization_id', t.organization_id::text,
  'workspace_id', t.workspace_id::text,
  'template_type', t.template_type,
  'eval_type', t.eval_type,
  'output_type_normalized', t.output_type_normalized,
  'deleted', t.deleted,
  'deleted_at_set', t.deleted_at IS NOT NULL,
  'version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id
  ),
  'default_version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id AND v.is_default = true
  ),
  'max_version_number', (
    SELECT COALESCE(max(v.version_number), 0)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id
  ),
  'ground_truth_id', t.config #>> '{ground_truth,ground_truth_id}',
  'ground_truth_enabled', (t.config #>> '{ground_truth,enabled}')::boolean
)
FROM template_row t;
`;
  return runPostgresJson(sql);
}

async function loadCustomEvalCreateDbAudit({
  templateId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH template_row AS (
  SELECT
    id,
    name,
    owner,
    organization_id,
    workspace_id,
    eval_tags,
    config,
    criteria,
    model,
    proxy_agi,
    visible_ui,
    deleted,
    deleted_at
  FROM model_hub_evaltemplate
  WHERE id = ${sqlUuid(templateId)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'template_id', t.id::text,
  'name', t.name,
  'owner', t.owner,
  'organization_id', t.organization_id::text,
  'workspace_id', t.workspace_id::text,
  'eval_tags', t.eval_tags,
  'criteria', t.criteria,
  'model', t.model,
  'proxy_agi', t.proxy_agi,
  'visible_ui', t.visible_ui,
  'deleted', t.deleted,
  'deleted_at_set', t.deleted_at IS NOT NULL,
  'required_keys', t.config->'required_keys',
  'config_output', t.config->>'output',
  'eval_type_id', t.config->>'eval_type_id',
  'custom_eval', (t.config->>'custom_eval')::boolean,
  'check_internet', (t.config->>'check_internet')::boolean,
  'version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id
  ),
  'default_version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id AND v.is_default = true
  ),
  'version_workspace_mismatch_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id
      AND (
        (v.workspace_id IS DISTINCT FROM t.workspace_id)
        OR (v.organization_id IS DISTINCT FROM t.organization_id)
      )
  )
)
FROM template_row t;
`;
  return runPostgresJson(sql);
}

async function loadLegacyEvalTemplateCreateAudit({
  templateName,
  systemOwnerName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH template_row AS (
  SELECT
    id,
    name,
    owner,
    organization_id,
    workspace_id,
    eval_tags,
    config
  FROM model_hub_evaltemplate
  WHERE name = ${sqlTextLiteral(templateName)}
    AND organization_id = ${sqlUuid(organizationId)}
    AND deleted = false
  ORDER BY created_at ASC
  LIMIT 1
)
SELECT COALESCE((
  SELECT json_build_object(
    'template_id', t.id::text,
    'name', t.name,
    'owner', t.owner,
    'organization_id', t.organization_id::text,
    'workspace_id', t.workspace_id::text,
    'eval_tags', t.eval_tags,
    'required_keys', t.config->'required_keys',
    'eval_type_id', t.config->>'eval_type_id',
    'active_template_count', (
      SELECT count(*)
      FROM model_hub_evaltemplate et
      WHERE et.name = ${sqlTextLiteral(templateName)}
        AND et.organization_id = ${sqlUuid(organizationId)}
        AND et.deleted = false
    ),
    'system_owner_count', (
      SELECT count(*)
      FROM model_hub_evaltemplate et
      WHERE et.name = ${sqlTextLiteral(systemOwnerName)}
        AND et.organization_id = ${sqlUuid(organizationId)}
        AND et.owner = 'system'
        AND et.deleted = false
    ),
    'version_count', (
      SELECT count(*)
      FROM model_hub_eval_template_version v
      WHERE v.eval_template_id = t.id
        AND v.deleted = false
    ),
    'default_version_count', (
      SELECT count(*)
      FROM model_hub_eval_template_version v
      WHERE v.eval_template_id = t.id
        AND v.is_default = true
        AND v.deleted = false
    ),
    'version_scope_mismatch_count', (
      SELECT count(*)
      FROM model_hub_eval_template_version v
      WHERE v.eval_template_id = t.id
        AND v.deleted = false
        AND (
          v.organization_id IS DISTINCT FROM t.organization_id
          OR v.workspace_id IS DISTINCT FROM t.workspace_id
        )
    )
  )
  FROM template_row t
), '{}'::json);
`;
  return runPostgresJson(sql);
}

async function hardDeleteLegacyEvalTemplateCreateFixture(
  templateNames,
  organizationId,
) {
  assert(Array.isArray(templateNames), "templateNames must be an array.");
  assert(isUuid(organizationId), "organizationId must be a UUID for cleanup.");
  const sql = `
WITH target_templates AS (
  SELECT id
  FROM model_hub_evaltemplate
  WHERE name = ANY(${sqlTextArray(templateNames)})
    AND organization_id = ${sqlUuid(organizationId)}
),
deleted_versions AS (
  DELETE FROM model_hub_eval_template_version
  WHERE eval_template_id IN (SELECT id FROM target_templates)
  RETURNING 1
),
deleted_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id IN (SELECT id FROM target_templates)
  RETURNING 1
)
SELECT json_build_object(
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_template_count', (SELECT count(*) FROM deleted_templates)
);
`;
  return runPostgresJson(sql);
}

async function loadEvalPlaygroundDbAudit({
  templateId,
  logId,
  feedbackId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(logId), "logId must be a UUID for DB audit.");
  assert(isUuid(feedbackId), "feedbackId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH ids AS (
  SELECT
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlUuid(logId)}::uuid AS log_id,
    ${sqlUuid(feedbackId)}::uuid AS feedback_id
)
SELECT json_build_object(
  'template_id', t.id::text,
  'template_organization_id', t.organization_id::text,
  'template_workspace_id', t.workspace_id::text,
  'template_deleted', t.deleted,
  'template_deleted_at_set', t.deleted_at IS NOT NULL,
  'log_id', l.log_id::text,
  'log_organization_id', l.organization_id::text,
  'log_workspace_id', l.workspace_id::text,
  'log_source', l.source,
  'log_source_id', l.source_id,
  'log_status', l.status,
  'log_deleted', l.deleted,
  'log_deleted_at_set', l.deleted_at IS NOT NULL,
  'feedback_id', f.id::text,
  'feedback_organization_id', f.organization_id::text,
  'feedback_workspace_id', f.workspace_id::text,
  'feedback_source', f.source,
  'feedback_source_id', f.source_id,
  'feedback_eval_template_id', f.eval_template_id::text,
  'feedback_value', f.value,
  'feedback_explanation', f.explanation,
  'feedback_action_type', f.action_type,
  'feedback_deleted', f.deleted,
  'feedback_absent_or_deleted', f.id IS NULL OR f.deleted = true
)
FROM ids
LEFT JOIN model_hub_evaltemplate t
  ON t.id = ids.template_id
LEFT JOIN usage_apicalllog l
  ON l.log_id = ids.log_id
LEFT JOIN model_hub_feedback f
  ON f.id = ids.feedback_id;
`;
  return runPostgresJson(sql);
}

async function loadTestEvaluationDbAudit({
  templateId,
  logIds,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(Array.isArray(logIds), "logIds must be an array for DB audit.");
  for (const logId of logIds) {
    assert(isUuid(logId), "logIds must only contain UUIDs for DB audit.");
  }
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH ids AS (
  SELECT
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlUuidArray(logIds)}::uuid[] AS log_ids
)
SELECT json_build_object(
  'template_id', t.id::text,
  'template_organization_id', t.organization_id::text,
  'template_workspace_id', t.workspace_id::text,
  'template_eval_type', t.eval_type,
  'template_eval_type_id', t.config ->> 'eval_type_id',
  'template_deleted', t.deleted,
  'template_deleted_at_set', t.deleted_at IS NOT NULL,
  'logs', COALESCE((
    SELECT json_agg(json_build_object(
      'log_id', l.log_id::text,
      'organization_id', l.organization_id::text,
      'workspace_id', l.workspace_id::text,
      'source', l.source,
      'source_id', l.source_id,
	  'status', l.status,
	  'deleted', l.deleted,
	  'deleted_at_set', l.deleted_at IS NOT NULL,
	  'output', cfg.config_json #>> '{output,output}',
	  'reason', cfg.config_json #>> '{output,reason}',
	  'required_keys', cfg.config_json -> 'required_keys',
	  'mappings', cfg.config_json -> 'mappings'
	) ORDER BY l.log_id)
	FROM usage_apicalllog l
	JOIN ids log_ids ON l.log_id = ANY(log_ids.log_ids)
	CROSS JOIN LATERAL (
	  SELECT CASE
	    WHEN jsonb_typeof(l.config::jsonb) = 'string'
	      THEN (l.config::jsonb #>> '{}')::jsonb
	    ELSE l.config::jsonb
	  END AS config_json
	) cfg
      ), '[]'::json)
    )
FROM ids
LEFT JOIN model_hub_evaltemplate t
  ON t.id = ids.template_id;
`;
  return runPostgresJson(sql);
}

async function loadEvalPlaygroundErrorLocalizerAudit({
  templateId,
  logId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(templateId), "templateId must be a UUID for localizer audit.");
  assert(isUuid(logId), "logId must be a UUID for localizer audit.");
  assert(
    isUuid(organizationId),
    "organizationId must be a UUID for localizer audit.",
  );
  if (workspaceId)
    assert(
      isUuid(workspaceId),
      "workspaceId must be a UUID for localizer audit.",
    );
  const sql = `
WITH task AS (
  SELECT *
  FROM error_localizer_task
  WHERE source_id = ${sqlUuid(logId)}
  ORDER BY created_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'task_exists', EXISTS(SELECT 1 FROM task),
  'task_id', (SELECT id::text FROM task),
  'task_source', (SELECT source FROM task),
  'task_source_id', (SELECT source_id::text FROM task),
  'task_eval_template_id', (SELECT eval_template_id::text FROM task),
  'task_organization_id', (SELECT organization_id::text FROM task),
  'task_workspace_id', (SELECT workspace_id::text FROM task),
  'task_status', (SELECT status FROM task),
  'task_input_data', (SELECT input_data FROM task),
  'task_input_keys', (SELECT input_keys FROM task),
  'task_input_types', (SELECT input_types FROM task),
  'task_eval_result', (SELECT eval_result FROM task),
  'task_error_message', (SELECT error_message FROM task),
  'task_deleted', (SELECT deleted FROM task),
  'task_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM task)
);
`;
  return runPostgresJson(sql);
}

async function loadEvalPlaygroundSettingsAudit({ templateId, userId, source }) {
  assert(isUuid(templateId), "templateId must be a UUID for settings audit.");
  assert(isUuid(userId), "userId must be a UUID for settings audit.");
  const sql = `
WITH setting AS (
  SELECT *
  FROM eval_settings
  WHERE eval_id = ${sqlUuid(templateId)}
    AND user_id = ${sqlUuid(userId)}
    AND source = ${sqlTextLiteral(source)}
  ORDER BY updated_at DESC
  LIMIT 1
)
SELECT json_build_object(
  'setting_exists', EXISTS(SELECT 1 FROM setting),
  'setting_count', (
    SELECT count(*)
    FROM eval_settings
    WHERE eval_id = ${sqlUuid(templateId)}
      AND user_id = ${sqlUuid(userId)}
      AND source = ${sqlTextLiteral(source)}
  ),
  'setting_deleted', (SELECT deleted FROM setting),
  'setting_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM setting),
  'column_count', (SELECT cardinality(column_config) FROM setting),
  'first_column_id', (SELECT column_config[1]->>'id' FROM setting),
  'first_column_visible', (SELECT (column_config[1]->>'is_visible')::boolean FROM setting)
);
`;
  return runPostgresJson(sql);
}

async function loadCompositeEvalDbAudit(
  compositeId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(compositeId), "compositeId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH template_row AS (
  SELECT
    id,
    name,
    owner,
    organization_id,
    workspace_id,
    template_type,
    aggregation_enabled,
    aggregation_function,
    composite_child_axis,
    deleted,
    deleted_at
  FROM model_hub_evaltemplate
  WHERE id = ${sqlUuid(compositeId)}
    AND organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'composite_id', t.id::text,
  'name', t.name,
  'owner', t.owner,
  'organization_id', t.organization_id::text,
  'workspace_id', t.workspace_id::text,
  'template_type', t.template_type,
  'aggregation_enabled', t.aggregation_enabled,
  'aggregation_function', t.aggregation_function,
  'composite_child_axis', t.composite_child_axis,
  'deleted', t.deleted,
  'deleted_at_set', t.deleted_at IS NOT NULL,
  'active_child_count', (
    SELECT count(*)
    FROM model_hub_composite_eval_child cec
    WHERE cec.parent_id = t.id AND cec.deleted = false
  ),
  'deleted_child_count', (
    SELECT count(*)
    FROM model_hub_composite_eval_child cec
    WHERE cec.parent_id = t.id AND cec.deleted = true
  ),
  'child_ids', (
    SELECT COALESCE(json_agg(cec.child_id::text ORDER BY cec.order), '[]'::json)
    FROM model_hub_composite_eval_child cec
    WHERE cec.parent_id = t.id AND cec.deleted = false
  ),
  'child_weights', (
    SELECT COALESCE(json_object_agg(cec.child_id::text, cec.weight), '{}'::json)
    FROM model_hub_composite_eval_child cec
    WHERE cec.parent_id = t.id AND cec.deleted = false
  ),
  'version_count', (
    SELECT count(*)
    FROM model_hub_eval_template_version v
    WHERE v.eval_template_id = t.id
  )
)
FROM template_row t;
`;
  return runPostgresJson(sql);
}

async function loadCompositeDatasetBindingDbAudit({
  datasetId,
  templateId,
  metricName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(datasetId)}::uuid AS dataset_id,
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlTextLiteral(metricName)}::text AS metric_name
),
metrics AS (
  SELECT
    uem.id,
    uem.name,
    uem.template_id,
    uem.organization_id,
    uem.workspace_id,
    uem.dataset_id,
    uem.status,
    uem.model,
    uem.error_localizer,
    uem.config,
    uem.composite_weight_overrides,
    uem.deleted,
    uem.deleted_at,
    uem.created_at,
    et.template_type
  FROM requested
  JOIN model_hub_userevalmetric uem
    ON uem.dataset_id = requested.dataset_id
   AND uem.template_id = requested.template_id
   AND uem.name = requested.metric_name
   AND uem.organization_id = ${sqlUuid(organizationId)}
  JOIN model_hub_evaltemplate et
    ON et.id = uem.template_id
)
SELECT json_build_object(
  'dataset_id', requested.dataset_id::text,
  'template_id', requested.template_id::text,
  'metric_name', requested.metric_name,
  'metric_count', (SELECT count(*) FROM metrics),
  'active_count', (SELECT count(*) FROM metrics WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM metrics WHERE deleted = true),
  'metrics', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'name', name,
          'template_id', template_id::text,
          'template_type', template_type,
          'organization_id', organization_id::text,
          'workspace_id', workspace_id::text,
          'dataset_id', dataset_id::text,
          'status', status,
          'model', model,
          'error_localizer', error_localizer,
          'config', config,
          'composite_weight_overrides', composite_weight_overrides,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM metrics
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
}

async function loadCompositeExperimentBindingDbAudit({
  experimentId,
  templateId,
  metricName,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(experimentId), "experimentId must be a UUID for DB audit.");
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(experimentId)}::uuid AS experiment_id,
    ${sqlUuid(templateId)}::uuid AS template_id,
    ${sqlTextLiteral(metricName)}::text AS metric_name
),
experiment_row AS (
  SELECT
    e.id,
    e.dataset_id,
    e.snapshot_dataset_id,
    COALESCE(e.snapshot_dataset_id, e.dataset_id) AS eval_dataset_id,
    d.organization_id,
    d.workspace_id
  FROM requested
  JOIN model_hub_experimentstable e
    ON e.id = requested.experiment_id
   AND e.deleted = false
  JOIN model_hub_dataset d
    ON d.id = e.dataset_id
   AND d.deleted = false
   AND d.organization_id = ${sqlUuid(organizationId)}
),
metrics AS (
  SELECT
    uem.id,
    uem.name,
    uem.template_id,
    uem.organization_id,
    uem.workspace_id,
    uem.dataset_id,
    uem.source_id,
    uem.status,
    uem.show_in_sidebar,
    uem.model,
    uem.error_localizer,
    uem.config,
    uem.composite_weight_overrides,
    uem.deleted,
    uem.deleted_at,
    uem.created_at,
    et.template_type,
    (
      SELECT count(*)
      FROM model_hub_experimentstable_user_eval_template_ids m
      WHERE m.experimentstable_id = requested.experiment_id
        AND m.userevalmetric_id = uem.id
    ) AS m2m_link_count
  FROM requested
  JOIN experiment_row e
    ON e.id = requested.experiment_id
  JOIN model_hub_userevalmetric uem
    ON uem.source_id = requested.experiment_id::text
   AND uem.template_id = requested.template_id
   AND uem.name = requested.metric_name
   AND uem.organization_id = ${sqlUuid(organizationId)}
  JOIN model_hub_evaltemplate et
    ON et.id = uem.template_id
)
SELECT json_build_object(
  'experiment_id', requested.experiment_id::text,
  'dataset_id', experiment_row.dataset_id::text,
  'snapshot_dataset_id', experiment_row.snapshot_dataset_id::text,
  'eval_dataset_id', experiment_row.eval_dataset_id::text,
  'dataset_organization_id', experiment_row.organization_id::text,
  'dataset_workspace_id', experiment_row.workspace_id::text,
  'template_id', requested.template_id::text,
  'metric_name', requested.metric_name,
  'experiment_active_metric_count', (
    SELECT count(*)
    FROM model_hub_experimentstable_user_eval_template_ids m
    JOIN model_hub_userevalmetric uem
      ON uem.id = m.userevalmetric_id
    WHERE m.experimentstable_id = requested.experiment_id
      AND uem.deleted = false
  ),
  'metric_count', (SELECT count(*) FROM metrics),
  'active_count', (SELECT count(*) FROM metrics WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM metrics WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM metrics WHERE deleted = true AND deleted_at IS NULL
  ),
  'metrics', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'name', name,
          'template_id', template_id::text,
          'template_type', template_type,
          'organization_id', organization_id::text,
          'workspace_id', workspace_id::text,
          'dataset_id', dataset_id::text,
          'source_id', source_id,
          'status', status,
          'show_in_sidebar', show_in_sidebar,
          'model', model,
          'error_localizer', error_localizer,
          'config', config,
          'composite_weight_overrides', composite_weight_overrides,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL,
          'm2m_link_count', m2m_link_count
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM metrics
  )
)
FROM requested
JOIN experiment_row ON experiment_row.id = requested.experiment_id;
`;
  return runPostgresJson(sql);
}

async function loadEvalSummaryTemplateDbAudit(templateId, organizationId) {
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  const sql = `
WITH requested_template AS (
  SELECT ${sqlUuid(templateId)}::uuid AS id
)
SELECT json_build_object(
  'template_id', requested_template.id::text,
  'row_exists', t.id IS NOT NULL,
  'name', t.name,
  'description', t.description,
  'criteria', t.criteria,
  'organization_id', t.organization_id::text
)
FROM requested_template
LEFT JOIN model_hub_evalsummarytemplate t
  ON t.id = requested_template.id;
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupDbAudit(groupId, organizationId, workspaceId) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested_group AS (
  SELECT ${sqlUuid(groupId)}::uuid AS id
)
SELECT json_build_object(
  'group_id', requested_group.id::text,
  'row_exists', eg.id IS NOT NULL,
  'name', eg.name,
  'description', eg.description,
  'organization_id', eg.organization_id::text,
  'workspace_id', eg.workspace_id::text,
  'deleted', eg.deleted,
  'deleted_at_set', eg.deleted_at IS NOT NULL,
  'relationship_count', (
    SELECT count(*)
    FROM model_hub_eval_group_eval_templates rel
    WHERE rel.evalgroup_id = requested_group.id
  ),
  'relationship_template_ids', (
    SELECT COALESCE(json_agg(rel.evaltemplate_id::text ORDER BY rel.evaltemplate_id::text), '[]'::json)
    FROM model_hub_eval_group_eval_templates rel
    WHERE rel.evalgroup_id = requested_group.id
  ),
  'add_history_count', (
    SELECT count(*)
    FROM model_hub_history h
    WHERE h.source_id = requested_group.id
      AND h.source_type = 'EVAL_GROUP'
      AND h.action = 'ADD'
  ),
  'delete_history_count', (
    SELECT count(*)
    FROM model_hub_history h
    WHERE h.source_id = requested_group.id
      AND h.source_type = 'EVAL_GROUP'
      AND h.action = 'DELETE'
  )
)
FROM requested_group
LEFT JOIN model_hub_eval_group eg
  ON eg.id = requested_group.id
  AND eg.organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupDatasetApplyDbAudit(
  groupId,
  datasetId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(datasetId), "datasetId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(groupId)}::uuid AS group_id,
    ${sqlUuid(datasetId)}::uuid AS dataset_id
),
metrics AS (
  SELECT
    uem.id,
    uem.template_id,
    uem.organization_id,
    uem.workspace_id,
    uem.status,
    uem.show_in_sidebar,
    uem.model,
    uem.error_localizer,
    uem.config,
    uem.deleted,
    uem.deleted_at,
    uem.created_at
  FROM requested
  JOIN model_hub_userevalmetric uem
    ON uem.eval_group_id = requested.group_id
   AND uem.dataset_id = requested.dataset_id
   AND uem.organization_id = ${sqlUuid(organizationId)}
)
SELECT json_build_object(
  'group_id', requested.group_id::text,
  'dataset_id', requested.dataset_id::text,
  'metric_count', (SELECT count(*) FROM metrics),
  'active_count', (SELECT count(*) FROM metrics WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM metrics WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM metrics WHERE deleted = true AND deleted_at IS NULL
  ),
  'metrics', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'template_id', template_id::text,
          'organization_id', organization_id::text,
          'workspace_id', workspace_id::text,
          'status', status,
          'show_in_sidebar', show_in_sidebar,
          'model', model,
          'error_localizer', error_localizer,
          'config', config,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM metrics
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupPromptApplyDbAudit(
  groupId,
  promptId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(groupId)}::uuid AS group_id,
    ${sqlUuid(promptId)}::uuid AS prompt_id
),
prompt AS (
  SELECT pt.id, pt.organization_id, pt.workspace_id
  FROM requested
  JOIN model_hub_prompttemplate pt
    ON pt.id = requested.prompt_id
   AND pt.organization_id = ${sqlUuid(organizationId)}
),
configs AS (
  SELECT
    pec.id,
    pec.eval_template_id,
    pec.prompt_template_id,
    pec.eval_group_id,
    pec.name,
    pec.mapping,
    pec.config,
    pec.status,
    pec.error_localizer,
    pec.deleted,
    pec.deleted_at,
    pec.created_at
  FROM requested
  JOIN model_hub_promptevalconfig pec
    ON pec.eval_group_id = requested.group_id
   AND pec.prompt_template_id = requested.prompt_id
)
SELECT json_build_object(
  'group_id', requested.group_id::text,
  'prompt_template_id', requested.prompt_id::text,
  'prompt_organization_id', prompt.organization_id::text,
  'prompt_workspace_id', prompt.workspace_id::text,
  'config_count', (SELECT count(*) FROM configs),
  'active_count', (SELECT count(*) FROM configs WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM configs WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM configs WHERE deleted = true AND deleted_at IS NULL
  ),
  'configs', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'eval_template_id', eval_template_id::text,
          'prompt_template_id', prompt_template_id::text,
          'eval_group_id', eval_group_id::text,
          'name', name,
          'mapping', mapping,
          'config', config,
          'status', status,
          'error_localizer', error_localizer,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM configs
  )
)
FROM requested
JOIN prompt ON prompt.id = requested.prompt_id;
`;
  return runPostgresJson(sql);
}

async function loadSimulateRunTestApplyCandidatesDbAudit(
  organizationId,
  workspaceId,
) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspacePredicate = workspaceId
    ? `AND rt.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
SELECT json_build_object(
  'organization_id', ${sqlUuid(organizationId)}::text,
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL"},
  'candidates', COALESCE(
    json_agg(
      json_build_object(
        'id', id::text,
        'name', name,
        'active_eval_config_count', active_eval_config_count
      )
      ORDER BY updated_at DESC
    ),
    '[]'::json
  )
)
FROM (
  SELECT
    rt.id,
    rt.name,
    rt.updated_at,
    count(sec.id) FILTER (WHERE sec.deleted = false) AS active_eval_config_count
  FROM simulate_run_test rt
  LEFT JOIN simulate_eval_config sec
    ON sec.run_test_id = rt.id
  WHERE rt.deleted = false
    AND rt.organization_id = ${sqlUuid(organizationId)}
    ${workspacePredicate}
  GROUP BY rt.id, rt.name, rt.updated_at
  HAVING count(sec.id) FILTER (WHERE sec.deleted = false) >= 1
  ORDER BY rt.updated_at DESC
  LIMIT 10
) candidates;
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupSimulateApplyDbAudit(
  groupId,
  runTestId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(runTestId), "runTestId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(groupId)}::uuid AS group_id,
    ${sqlUuid(runTestId)}::uuid AS run_test_id
),
run_test AS (
  SELECT rt.id, rt.organization_id, rt.workspace_id
  FROM requested
  JOIN simulate_run_test rt
    ON rt.id = requested.run_test_id
   AND rt.organization_id = ${sqlUuid(organizationId)}
),
configs AS (
  SELECT
    sec.id,
    sec.eval_template_id,
    sec.run_test_id,
    sec.eval_group_id,
    sec.name,
    sec.mapping,
    sec.config,
    sec.filters,
    sec.status,
    sec.model,
    sec.error_localizer,
    sec.deleted,
    sec.deleted_at,
    sec.created_at
  FROM requested
  JOIN simulate_eval_config sec
    ON sec.eval_group_id = requested.group_id
   AND sec.run_test_id = requested.run_test_id
)
SELECT json_build_object(
  'group_id', requested.group_id::text,
  'run_test_id', requested.run_test_id::text,
  'run_test_organization_id', run_test.organization_id::text,
  'run_test_workspace_id', run_test.workspace_id::text,
  'run_test_active_config_count', (
    SELECT count(*)
    FROM simulate_eval_config sec
    WHERE sec.run_test_id = requested.run_test_id
      AND sec.deleted = false
  ),
  'config_count', (SELECT count(*) FROM configs),
  'active_count', (SELECT count(*) FROM configs WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM configs WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM configs WHERE deleted = true AND deleted_at IS NULL
  ),
  'configs', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'eval_template_id', eval_template_id::text,
          'run_test_id', run_test_id::text,
          'eval_group_id', eval_group_id::text,
          'name', name,
          'mapping', mapping,
          'config', config,
          'filters', filters,
          'status', status,
          'model', model,
          'error_localizer', error_localizer,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM configs
  )
)
FROM requested
JOIN run_test ON run_test.id = requested.run_test_id;
`;
  return runPostgresJson(sql);
}

async function loadExperimentApplyCandidatesDbAudit(
  organizationId,
  workspaceId,
) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspacePredicate = workspaceId
    ? `AND d.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
SELECT json_build_object(
  'organization_id', ${sqlUuid(organizationId)}::text,
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL"},
  'candidates', COALESCE(
    json_agg(
      json_build_object(
        'id', id::text,
        'name', name,
        'dataset_id', dataset_id::text,
        'dataset_name', dataset_name,
        'active_eval_metric_count', active_eval_metric_count,
        'columns', columns
      )
      ORDER BY active_eval_metric_count DESC, updated_at DESC
    ),
    '[]'::json
  )
)
FROM (
  SELECT
    e.id,
    e.name,
    e.updated_at,
    d.id AS dataset_id,
    d.name AS dataset_name,
    count(DISTINCT uem.id) FILTER (WHERE uem.deleted = false)
      AS active_eval_metric_count,
    COALESCE(
      json_agg(
        DISTINCT jsonb_build_object(
          'id', c.id::text,
          'name', c.name,
          'source', c.source
        )
      ) FILTER (WHERE c.id IS NOT NULL),
      '[]'::json
    ) AS columns
  FROM model_hub_experimentstable e
  JOIN model_hub_dataset d
    ON d.id = e.dataset_id
   AND d.deleted = false
   AND d.organization_id = ${sqlUuid(organizationId)}
   ${workspacePredicate}
  LEFT JOIN model_hub_experimentstable_user_eval_template_ids m
    ON m.experimentstable_id = e.id
  LEFT JOIN model_hub_userevalmetric uem
    ON uem.id = m.userevalmetric_id
  LEFT JOIN model_hub_column c
    ON c.dataset_id = d.id
   AND c.deleted = false
   AND c.id::text = ANY(d.column_order)
   AND c.source NOT IN (
     'experiment',
     'experiment_evaluation_tags',
     'evaluation_tags',
     'optimisation_evaluation_tags',
     'evaluation',
     'experiment_evaluation',
     'optimisation_evaluation',
     'evaluation_reason'
   )
  WHERE e.deleted = false
  GROUP BY e.id, e.name, e.updated_at, d.id, d.name
  HAVING count(DISTINCT uem.id) FILTER (WHERE uem.deleted = false) >= 1
     AND count(DISTINCT c.id) >= 1
  ORDER BY active_eval_metric_count DESC, e.updated_at DESC
  LIMIT 10
) candidates;
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupExperimentApplyDbAudit(
  groupId,
  experimentId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(experimentId), "experimentId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(groupId)}::uuid AS group_id,
    ${sqlUuid(experimentId)}::uuid AS experiment_id
),
experiment_row AS (
  SELECT e.id, e.dataset_id, d.organization_id, d.workspace_id
  FROM requested
  JOIN model_hub_experimentstable e
    ON e.id = requested.experiment_id
   AND e.deleted = false
  JOIN model_hub_dataset d
    ON d.id = e.dataset_id
   AND d.deleted = false
   AND d.organization_id = ${sqlUuid(organizationId)}
),
metrics AS (
  SELECT
    uem.id,
    uem.template_id,
    uem.organization_id,
    uem.workspace_id,
    uem.dataset_id,
    uem.source_id,
    uem.status,
    uem.show_in_sidebar,
    uem.model,
    uem.error_localizer,
    uem.config,
    uem.deleted,
    uem.deleted_at,
    uem.created_at,
    (
      SELECT count(*)
      FROM model_hub_experimentstable_user_eval_template_ids m
      WHERE m.experimentstable_id = requested.experiment_id
        AND m.userevalmetric_id = uem.id
    ) AS m2m_link_count
  FROM requested
  JOIN experiment_row e
    ON e.id = requested.experiment_id
  JOIN model_hub_userevalmetric uem
    ON uem.eval_group_id = requested.group_id
   AND uem.source_id = requested.experiment_id::text
   AND uem.dataset_id = e.dataset_id
)
SELECT json_build_object(
  'group_id', requested.group_id::text,
  'experiment_id', requested.experiment_id::text,
  'dataset_id', experiment_row.dataset_id::text,
  'dataset_organization_id', experiment_row.organization_id::text,
  'dataset_workspace_id', experiment_row.workspace_id::text,
  'experiment_active_metric_count', (
    SELECT count(*)
    FROM model_hub_experimentstable_user_eval_template_ids m
    JOIN model_hub_userevalmetric uem
      ON uem.id = m.userevalmetric_id
    WHERE m.experimentstable_id = requested.experiment_id
      AND uem.deleted = false
  ),
  'metric_count', (SELECT count(*) FROM metrics),
  'active_count', (SELECT count(*) FROM metrics WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM metrics WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM metrics WHERE deleted = true AND deleted_at IS NULL
  ),
  'metrics', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'template_id', template_id::text,
          'organization_id', organization_id::text,
          'workspace_id', workspace_id::text,
          'dataset_id', dataset_id::text,
          'source_id', source_id,
          'status', status,
          'show_in_sidebar', show_in_sidebar,
          'model', model,
          'error_localizer', error_localizer,
          'config', config,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL,
          'm2m_link_count', m2m_link_count
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM metrics
  )
)
FROM requested
JOIN experiment_row ON experiment_row.id = requested.experiment_id;
`;
  return runPostgresJson(sql);
}

async function loadEvalTaskProjectApplyCandidatesDbAudit(
  organizationId,
  workspaceId,
) {
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspacePredicate = workspaceId
    ? `AND p.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
SELECT json_build_object(
  'organization_id', ${sqlUuid(organizationId)}::text,
  'workspace_id', ${workspaceId ? `${sqlUuid(workspaceId)}::text` : "NULL"},
  'candidates', COALESCE(
    json_agg(
      json_build_object(
        'id', id::text,
        'name', name,
        'trace_type', trace_type,
        'workspace_id', workspace_id::text,
        'active_config_count', active_config_count
      )
      ORDER BY active_config_count DESC, updated_at DESC
    ),
    '[]'::json
  )
)
FROM (
  SELECT
    p.id,
    p.name,
    p.trace_type,
    p.workspace_id,
    p.updated_at,
    count(cec.id) FILTER (WHERE cec.deleted = false) AS active_config_count
  FROM tracer_project p
  LEFT JOIN tracer_custom_eval_config cec
    ON cec.project_id = p.id
  WHERE p.deleted = false
    AND p.organization_id = ${sqlUuid(organizationId)}
    AND p.trace_type = 'observe'
    ${workspacePredicate}
  GROUP BY p.id, p.name, p.trace_type, p.workspace_id, p.updated_at
  ORDER BY active_config_count DESC, p.updated_at DESC
  LIMIT 10
) candidates;
`;
  return runPostgresJson(sql);
}

async function loadEvalGroupEvalTaskApplyDbAudit(
  groupId,
  projectId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groupId), "groupId must be a UUID for DB audit.");
  assert(isUuid(projectId), "projectId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(groupId)}::uuid AS group_id,
    ${sqlUuid(projectId)}::uuid AS project_id
),
project_row AS (
  SELECT p.id, p.organization_id, p.workspace_id, p.trace_type
  FROM requested
  JOIN tracer_project p
    ON p.id = requested.project_id
   AND p.deleted = false
   AND p.organization_id = ${sqlUuid(organizationId)}
),
configs AS (
  SELECT
    cec.id,
    cec.eval_template_id,
    cec.project_id,
    cec.eval_group_id,
    cec.name,
    cec.mapping,
    cec.config,
    cec.filters,
    cec.model,
    cec.error_localizer,
    cec.deleted,
    cec.deleted_at,
    cec.created_at
  FROM requested
  JOIN tracer_custom_eval_config cec
    ON cec.eval_group_id = requested.group_id
   AND cec.project_id = requested.project_id
)
SELECT json_build_object(
  'group_id', requested.group_id::text,
  'project_id', requested.project_id::text,
  'project_organization_id', project_row.organization_id::text,
  'project_workspace_id', project_row.workspace_id::text,
  'project_trace_type', project_row.trace_type,
  'project_active_config_count', (
    SELECT count(*)
    FROM tracer_custom_eval_config cec
    WHERE cec.project_id = requested.project_id
      AND cec.deleted = false
  ),
  'config_count', (SELECT count(*) FROM configs),
  'active_count', (SELECT count(*) FROM configs WHERE deleted = false),
  'deleted_count', (SELECT count(*) FROM configs WHERE deleted = true),
  'deleted_without_deleted_at_count', (
    SELECT count(*) FROM configs WHERE deleted = true AND deleted_at IS NULL
  ),
  'configs', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'eval_template_id', eval_template_id::text,
          'project_id', project_id::text,
          'eval_group_id', eval_group_id::text,
          'name', name,
          'mapping', mapping,
          'config', config,
          'filters', filters,
          'model', model,
          'error_localizer', error_localizer,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL
        )
        ORDER BY created_at
      ),
      '[]'::json
    )
    FROM configs
  )
)
FROM requested
JOIN project_row ON project_row.id = requested.project_id;
`;
  return runPostgresJson(sql);
}

async function loadGroundTruthDbAudit(
  groundTruthId,
  templateId,
  organizationId,
  workspaceId,
) {
  assert(isUuid(groundTruthId), "groundTruthId must be a UUID for DB audit.");
  assert(isUuid(templateId), "templateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
SELECT json_build_object(
  'ground_truth_id', gt.id::text,
  'template_id', gt.eval_template_id::text,
  'organization_id', gt.organization_id::text,
  'workspace_id', gt.workspace_id::text,
  'name', gt.name,
  'row_count', gt.row_count,
  'columns', gt.columns,
  'variable_mapping', gt.variable_mapping,
  'role_mapping', gt.role_mapping,
  'embedding_status', gt.embedding_status,
  'deleted', gt.deleted,
  'deleted_at_set', gt.deleted_at IS NOT NULL
)
FROM model_hub_eval_ground_truth gt
WHERE gt.id = ${sqlUuid(groundTruthId)}
  AND gt.eval_template_id = ${sqlUuid(templateId)}
  AND gt.organization_id = ${sqlUuid(organizationId)};
`;
  return runPostgresJson(sql);
}

async function setGroundTruthEmbeddingStatus({
  groundTruthId,
  embeddingStatus,
  embeddedRowCount = 0,
}) {
  assert(isUuid(groundTruthId), "groundTruthId must be a UUID for DB update.");
  assert(
    ["pending", "processing", "completed", "failed"].includes(embeddingStatus),
    "Invalid ground-truth embedding status for DB update.",
  );
  const sql = `
WITH updated AS (
  UPDATE model_hub_eval_ground_truth
  SET
    embedding_status = ${sqlTextLiteral(embeddingStatus)},
    embedded_row_count = ${Number(embeddedRowCount) || 0},
    updated_at = now()
  WHERE id = ${sqlUuid(groundTruthId)}
  RETURNING id, embedding_status, embedded_row_count
)
SELECT json_build_object(
  'updated_count', (SELECT count(*) FROM updated),
  'ground_truth_id', (SELECT id::text FROM updated),
  'embedding_status', (SELECT embedding_status FROM updated),
  'embedded_row_count', (SELECT embedded_row_count FROM updated)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.updated_count) === 1,
    "Ground-truth embedding status DB update did not affect one row.",
  );
  return result;
}

async function moveGroundTruthRetrievalFixtureToWorkspace({
  templateId,
  groundTruthId,
  workspaceId,
}) {
  assert(isUuid(templateId), "templateId must be a UUID for DB update.");
  assert(isUuid(groundTruthId), "groundTruthId must be a UUID for DB update.");
  assert(isUuid(workspaceId), "workspaceId must be a UUID for DB update.");
  const sql = `
WITH updated_template AS (
  UPDATE model_hub_evaltemplate
  SET workspace_id = ${sqlUuid(workspaceId)}, updated_at = now()
  WHERE id = ${sqlUuid(templateId)}
  RETURNING id
),
updated_versions AS (
  UPDATE model_hub_eval_template_version
  SET workspace_id = ${sqlUuid(workspaceId)}, updated_at = now()
  WHERE eval_template_id = ${sqlUuid(templateId)}
  RETURNING id
),
updated_ground_truth AS (
  UPDATE model_hub_eval_ground_truth
  SET workspace_id = ${sqlUuid(workspaceId)}, updated_at = now()
  WHERE id = ${sqlUuid(groundTruthId)}
    AND eval_template_id = ${sqlUuid(templateId)}
  RETURNING id
)
SELECT json_build_object(
  'updated_template_count', (SELECT count(*) FROM updated_template),
  'updated_version_count', (SELECT count(*) FROM updated_versions),
  'updated_ground_truth_count', (SELECT count(*) FROM updated_ground_truth)
);
`;
  const result = await runPostgresJson(sql);
  assert(
    Number(result.updated_template_count) === 1 &&
      Number(result.updated_ground_truth_count) === 1,
    "Ground-truth retrieval fixture workspace move did not update expected rows.",
  );
  return result;
}

async function loadGroundTruthRetrievalAudit({
  groundTruthIds,
  organizationId,
}) {
  assert(
    Array.isArray(groundTruthIds) && groundTruthIds.every(isUuid),
    "groundTruthIds must be UUIDs for DB audit.",
  );
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT unnest(${sqlUuidArray(groundTruthIds)}) AS ground_truth_id
),
ground_truths AS (
  SELECT
    gt.id,
    gt.eval_template_id,
    gt.organization_id,
    gt.workspace_id,
    gt.name,
    gt.row_count,
    gt.embedding_status,
    gt.embedded_row_count,
    gt.deleted,
    gt.deleted_at,
    et.workspace_id AS template_workspace_id,
    et.deleted AS template_deleted
  FROM requested
  JOIN model_hub_eval_ground_truth gt
    ON gt.id = requested.ground_truth_id
   AND gt.organization_id = ${sqlUuid(organizationId)}
  JOIN model_hub_evaltemplate et
    ON et.id = gt.eval_template_id
)
SELECT json_build_object(
  'ground_truths', COALESCE((
    SELECT json_agg(
      json_build_object(
        'ground_truth_id', id::text,
        'template_id', eval_template_id::text,
        'organization_id', organization_id::text,
        'workspace_id', workspace_id::text,
        'template_workspace_id', template_workspace_id::text,
        'name', name,
        'row_count', row_count,
        'embedding_status', embedding_status,
        'embedded_row_count', embedded_row_count,
        'deleted', deleted,
        'deleted_at_set', deleted_at IS NOT NULL,
        'template_deleted', template_deleted
      )
      ORDER BY name
    )
    FROM ground_truths
  ), '[]'::json),
  'ground_truth_count', (SELECT count(*) FROM ground_truths),
  'embedding_count', (
    SELECT count(*)
    FROM model_hub_eval_ground_truth_embedding emb
    WHERE emb.ground_truth_id = ANY(${sqlUuidArray(groundTruthIds)})
  )
);
`;
  return runPostgresJson(sql);
}

function findGroundTruthAudit(audit, groundTruthId) {
  const row = payloadArray(audit?.ground_truths, "ground_truths").find(
    (candidate) => candidate?.ground_truth_id === groundTruthId,
  );
  assert(row, "Ground-truth retrieval audit did not find expected row.");
  return row;
}

async function hardDeleteGroundTruthRetrievalFixture(
  templateIds,
  organizationId,
) {
  const ids = (templateIds || []).filter(isUuid);
  assert(isUuid(organizationId), "organizationId must be a UUID for cleanup.");
  if (!ids.length) {
    return {
      deleted_embedding_count: 0,
      deleted_ground_truth_count: 0,
      deleted_version_count: 0,
      deleted_template_count: 0,
    };
  }
  const sql = `
WITH target_templates AS (
  SELECT id
  FROM model_hub_evaltemplate
  WHERE id = ANY(${sqlUuidArray(ids)})
    AND organization_id = ${sqlUuid(organizationId)}
),
target_ground_truths AS (
  SELECT id
  FROM model_hub_eval_ground_truth
  WHERE eval_template_id IN (SELECT id FROM target_templates)
),
deleted_embeddings AS (
  DELETE FROM model_hub_eval_ground_truth_embedding
  WHERE ground_truth_id IN (SELECT id FROM target_ground_truths)
  RETURNING 1
),
deleted_ground_truths AS (
  DELETE FROM model_hub_eval_ground_truth
  WHERE id IN (SELECT id FROM target_ground_truths)
  RETURNING 1
),
deleted_versions AS (
  DELETE FROM model_hub_eval_template_version
  WHERE eval_template_id IN (SELECT id FROM target_templates)
  RETURNING 1
),
deleted_templates AS (
  DELETE FROM model_hub_evaltemplate
  WHERE id IN (SELECT id FROM target_templates)
  RETURNING 1
)
SELECT json_build_object(
  'deleted_embedding_count', (SELECT count(*) FROM deleted_embeddings),
  'deleted_ground_truth_count', (SELECT count(*) FROM deleted_ground_truths),
  'deleted_version_count', (SELECT count(*) FROM deleted_versions),
  'deleted_template_count', (SELECT count(*) FROM deleted_templates)
);
`;
  return runPostgresJson(sql);
}

async function loadPromptWorkbenchDbAudit({
  folderId,
  promptId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(folderId), "folderId must be a UUID for DB audit.");
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT
    ${sqlUuid(folderId)}::uuid AS folder_id,
    ${sqlUuid(promptId)}::uuid AS prompt_id
),
folder AS (
  SELECT
    id,
    name,
    organization_id,
    workspace_id,
    parent_folder_id,
    is_sample,
    deleted,
    deleted_at
  FROM model_hub_prompt_folder
  WHERE id = (SELECT folder_id FROM requested)
),
prompt AS (
  SELECT
    id,
    name,
    description,
    organization_id,
    workspace_id,
    prompt_folder_id,
    variable_names,
    deleted,
    deleted_at
  FROM model_hub_prompttemplate
  WHERE id = (SELECT prompt_id FROM requested)
),
versions AS (
  SELECT
    id,
    original_template_id,
    template_version,
    is_draft,
    is_default,
    deleted,
    deleted_at,
    prompt_config_snapshot
  FROM model_hub_promptversion
  WHERE original_template_id = (SELECT prompt_id FROM requested)
)
SELECT json_build_object(
  'folder_id', (SELECT id::text FROM folder),
  'folder_name', (SELECT name FROM folder),
  'folder_organization_id', (SELECT organization_id::text FROM folder),
  'folder_workspace_id', (SELECT workspace_id::text FROM folder),
  'folder_parent_folder_id', (SELECT parent_folder_id::text FROM folder),
  'folder_is_sample', (SELECT is_sample FROM folder),
  'folder_deleted', (SELECT deleted FROM folder),
  'folder_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM folder),
  'prompt_id', (SELECT id::text FROM prompt),
  'prompt_name', (SELECT name FROM prompt),
  'prompt_description', (SELECT description FROM prompt),
  'prompt_organization_id', (SELECT organization_id::text FROM prompt),
  'prompt_workspace_id', (SELECT workspace_id::text FROM prompt),
  'prompt_folder_id', (SELECT prompt_folder_id::text FROM prompt),
  'prompt_variable_names', (SELECT variable_names FROM prompt),
  'prompt_deleted', (SELECT deleted FROM prompt),
  'prompt_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM prompt),
  'version_count', (SELECT count(*) FROM versions),
  'active_version_count', (SELECT count(*) FROM versions WHERE deleted = false),
  'deleted_version_count', (SELECT count(*) FROM versions WHERE deleted = true),
  'deleted_versions_without_deleted_at_count', (
    SELECT count(*) FROM versions WHERE deleted = true AND deleted_at IS NULL
  ),
  'versions', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'template_version', template_version,
          'is_draft', is_draft,
          'is_default', is_default,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL,
          'prompt_config_snapshot', prompt_config_snapshot
        )
        ORDER BY template_version
      ),
      '[]'::json
    )
    FROM versions
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
}

function assertPromptWorkbenchDbAudit(
  audit,
  {
    folderId,
    promptId,
    organizationId,
    workspaceId,
    expectedFolderDeleted,
    expectedPromptDeleted,
    expectedVersionsDeleted,
  },
) {
  assert(audit?.folder_id === folderId, "Prompt folder DB audit id mismatch.");
  assert(audit?.prompt_id === promptId, "Prompt DB audit id mismatch.");
  assert(
    audit.folder_organization_id === organizationId,
    "Prompt folder DB audit organization mismatch.",
  );
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.folder_workspace_id === workspaceId,
      "Prompt folder DB audit workspace mismatch.",
    );
    assert(
      audit.prompt_workspace_id === workspaceId,
      "Prompt DB audit workspace mismatch.",
    );
  }
  assert(
    audit.prompt_folder_id === folderId,
    "Prompt DB audit did not preserve folder assignment.",
  );
  assert(
    Number(audit.version_count) > 0,
    "Prompt DB audit found no prompt versions.",
  );
  assert(
    audit.folder_deleted === expectedFolderDeleted,
    "Prompt folder DB deleted state mismatch.",
  );
  assert(
    audit.prompt_deleted === expectedPromptDeleted,
    "Prompt DB deleted state mismatch.",
  );

  if (expectedFolderDeleted) {
    assert(
      audit.folder_deleted_at_set === true,
      "Deleted prompt folder missing deleted_at.",
    );
  }
  if (expectedPromptDeleted) {
    assert(
      audit.prompt_deleted_at_set === true,
      "Deleted prompt missing deleted_at.",
    );
  }

  if (expectedVersionsDeleted) {
    assert(
      Number(audit.deleted_version_count) === Number(audit.version_count),
      "Not all prompt versions were marked deleted.",
    );
    assert(
      Number(audit.deleted_versions_without_deleted_at_count) === 0,
      "Deleted prompt versions were missing deleted_at.",
    );
  } else {
    assert(
      Number(audit.active_version_count) > 0,
      "Active prompt had no active versions.",
    );
  }
}

async function loadPromptTemplateVersionDbAudit({
  promptId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const sql = `
WITH requested AS (
  SELECT ${sqlUuid(promptId)}::uuid AS prompt_id
),
prompt AS (
  SELECT
    id,
    name,
    organization_id,
    workspace_id,
    deleted,
    deleted_at
  FROM model_hub_prompttemplate
  WHERE id = (SELECT prompt_id FROM requested)
),
versions AS (
  SELECT
    id,
    original_template_id,
    template_version,
    is_draft,
    is_default,
    deleted,
    deleted_at,
    prompt_config_snapshot
  FROM model_hub_promptversion
  WHERE original_template_id = (SELECT prompt_id FROM requested)
)
SELECT json_build_object(
  'prompt_id', (SELECT id::text FROM prompt),
  'prompt_name', (SELECT name FROM prompt),
  'prompt_organization_id', (SELECT organization_id::text FROM prompt),
  'prompt_workspace_id', (SELECT workspace_id::text FROM prompt),
  'prompt_deleted', (SELECT deleted FROM prompt),
  'prompt_deleted_at_set', (SELECT deleted_at IS NOT NULL FROM prompt),
  'version_count', (SELECT count(*) FROM versions),
  'active_version_count', (SELECT count(*) FROM versions WHERE deleted = false),
  'deleted_version_count', (SELECT count(*) FROM versions WHERE deleted = true),
  'active_default_version_count', (
    SELECT count(*) FROM versions WHERE deleted = false AND is_default = true
  ),
  'deleted_versions_without_deleted_at_count', (
    SELECT count(*) FROM versions WHERE deleted = true AND deleted_at IS NULL
  ),
  'active_default_versions', (
    SELECT COALESCE(json_agg(template_version ORDER BY template_version), '[]'::json)
    FROM versions
    WHERE deleted = false AND is_default = true
  ),
  'all_default_versions', (
    SELECT COALESCE(json_agg(template_version ORDER BY template_version), '[]'::json)
    FROM versions
    WHERE is_default = true
  ),
  'versions', (
    SELECT COALESCE(
      json_agg(
        json_build_object(
          'id', id::text,
          'template_version', template_version,
          'is_draft', is_draft,
          'is_default', is_default,
          'deleted', deleted,
          'deleted_at_set', deleted_at IS NOT NULL,
          'prompt_config_snapshot', prompt_config_snapshot
        )
        ORDER BY template_version
      ),
      '[]'::json
    )
    FROM versions
  )
)
FROM requested;
`;
  return runPostgresJson(sql);
}

async function loadPromptRunPreviewDbAudit({
  promptId,
  templateVersion,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspaceFilter = workspaceId
    ? `AND p.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH prompt AS (
  SELECT
    id,
    name,
    organization_id,
    workspace_id,
    deleted,
    deleted_at
  FROM model_hub_prompttemplate p
  WHERE p.id = ${sqlUuid(promptId)}
    AND p.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
),
version AS (
  SELECT
    id,
    original_template_id,
    template_version,
    is_draft,
    deleted,
    deleted_at,
    prompt_config_snapshot,
    output,
    metadata
  FROM model_hub_promptversion
  WHERE original_template_id = (SELECT id FROM prompt)
    AND template_version = ${sqlText(templateVersion)}
)
SELECT json_build_object(
  'prompt_id', p.id::text,
  'prompt_name', p.name,
  'prompt_organization_id', p.organization_id::text,
  'prompt_workspace_id', p.workspace_id::text,
  'prompt_deleted', p.deleted,
  'prompt_deleted_at_set', p.deleted_at IS NOT NULL,
  'version_id', v.id::text,
  'template_version', v.template_version,
  'is_draft', v.is_draft,
  'version_deleted', v.deleted,
  'version_deleted_at_set', v.deleted_at IS NOT NULL,
  'model_name', v.prompt_config_snapshot->'configuration'->>'model',
  'output', v.output,
  'metadata', v.metadata,
  'output_count', CASE
    WHEN jsonb_typeof(v.output) = 'array' THEN jsonb_array_length(v.output)
    ELSE 0
  END,
  'metadata_count', CASE
    WHEN jsonb_typeof(v.metadata) = 'array' THEN jsonb_array_length(v.metadata)
    ELSE 0
  END
)
FROM prompt p
LEFT JOIN version v
  ON v.original_template_id = p.id;
`;
  return runPostgresJson(sql);
}

function assertPromptRunPreviewDbAudit(
  audit,
  { promptId, organizationId, workspaceId, modelName, expectedDeleted },
) {
  assert(audit?.prompt_id === promptId, "Prompt run DB audit id mismatch.");
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt run DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.prompt_workspace_id === workspaceId,
      "Prompt run DB audit workspace mismatch.",
    );
  }
  assert(
    audit.template_version === "v1",
    "Prompt run DB audit returned the wrong version.",
  );
  assert(
    audit.model_name === modelName,
    "Prompt run DB audit returned the wrong model.",
  );

  if (expectedDeleted) {
    assert(audit.prompt_deleted === true, "Prompt run prompt should be deleted.");
    assert(
      audit.prompt_deleted_at_set === true,
      "Deleted prompt run prompt missing deleted_at.",
    );
    assert(
      audit.version_deleted === true,
      "Prompt run version should be deleted.",
    );
    assert(
      audit.version_deleted_at_set === true,
      "Deleted prompt run version missing deleted_at.",
    );
    return;
  }

  assert(audit.prompt_deleted === false, "Prompt run prompt should be active.");
  assert(audit.version_deleted === false, "Prompt run version should be active.");
  assert(audit.is_draft === false, "Prompt run version should be non-draft.");
  assert(Number(audit.output_count) > 0, "Prompt run DB audit found no output.");
  assert(
    Number(audit.metadata_count) > 0,
    "Prompt run DB audit found no metadata.",
  );
}

function assertPromptTemplateVersionDbAudit(
  audit,
  {
    promptId,
    organizationId,
    workspaceId,
    expectedDeleted,
    expectedDefaultVersion,
  },
) {
  assert(audit?.prompt_id === promptId, "Prompt DB audit id mismatch.");
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.prompt_workspace_id === workspaceId,
      "Prompt DB audit workspace mismatch.",
    );
  }
  assert(Number(audit.version_count) >= 2, "Prompt DB audit found too few versions.");
  const allDefaultVersions = payloadArray(
    audit.all_default_versions,
    "all_default_versions",
  );
  assert(
    allDefaultVersions.length === 1 &&
      allDefaultVersions[0] === expectedDefaultVersion,
    `Prompt DB default version should be unique ${expectedDefaultVersion}: ${JSON.stringify(
      audit,
    )}.`,
  );

  if (expectedDeleted) {
    assert(audit.prompt_deleted === true, "Prompt should be soft-deleted.");
    assert(audit.prompt_deleted_at_set === true, "Deleted prompt missing deleted_at.");
    assert(
      Number(audit.active_version_count) === 0,
      "Deleted prompt still had active versions.",
    );
    assert(
      Number(audit.deleted_version_count) === Number(audit.version_count),
      "Deleted prompt did not soft-delete all versions.",
    );
    assert(
      Number(audit.deleted_versions_without_deleted_at_count) === 0,
      "Deleted prompt versions were missing deleted_at.",
    );
    return;
  }

  assert(audit.prompt_deleted === false, "Prompt should still be active.");
  assert(
    Number(audit.active_default_version_count) === 1,
    "Active prompt should have exactly one active default version.",
  );
  assert(
    payloadArray(audit.active_default_versions, "active_default_versions")[0] ===
      expectedDefaultVersion,
    "Active prompt default version mismatch.",
  );
}

async function loadPromptEvalConfigDbAudit({
  promptId,
  evalConfigId,
  evalTemplateId,
  organizationId,
  workspaceId,
}) {
  assert(isUuid(promptId), "promptId must be a UUID for DB audit.");
  assert(isUuid(evalConfigId), "evalConfigId must be a UUID for DB audit.");
  assert(isUuid(evalTemplateId), "evalTemplateId must be a UUID for DB audit.");
  assert(isUuid(organizationId), "organizationId must be a UUID for DB audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for DB audit.");
  const workspaceFilter = workspaceId
    ? `AND p.workspace_id = ${sqlUuid(workspaceId)}`
    : "";
  const sql = `
WITH prompt AS (
  SELECT
    id,
    name,
    organization_id,
    workspace_id,
    deleted
  FROM model_hub_prompttemplate p
  WHERE p.id = ${sqlUuid(promptId)}
    AND p.organization_id = ${sqlUuid(organizationId)}
    ${workspaceFilter}
),
config AS (
  SELECT
    id,
    name,
    prompt_template_id,
    eval_template_id,
    mapping,
    config,
    status,
    error_localizer,
    deleted,
    deleted_at
  FROM model_hub_promptevalconfig
  WHERE id = ${sqlUuid(evalConfigId)}
),
eval_template AS (
  SELECT
    id,
    name,
    owner,
    config
  FROM model_hub_evaltemplate
  WHERE id = ${sqlUuid(evalTemplateId)}
)
SELECT json_build_object(
  'prompt_id', p.id::text,
  'prompt_name', p.name,
  'prompt_organization_id', p.organization_id::text,
  'prompt_workspace_id', p.workspace_id::text,
  'prompt_deleted', p.deleted,
  'config_id', c.id::text,
  'config_name', c.name,
  'config_prompt_template_id', c.prompt_template_id::text,
  'eval_template_id', c.eval_template_id::text,
  'eval_template_name', et.name,
  'eval_template_owner', et.owner,
  'eval_template_config', et.config,
  'mapping', c.mapping,
  'config', c.config,
  'status', c.status,
  'error_localizer', c.error_localizer,
  'deleted', c.deleted,
  'deleted_at_set', c.deleted_at IS NOT NULL
)
FROM prompt p
LEFT JOIN config c
  ON c.prompt_template_id = p.id
LEFT JOIN eval_template et
  ON et.id = c.eval_template_id;
`;
  return runPostgresJson(sql);
}

function assertPromptEvalConfigDbAudit(
  audit,
  {
    promptId,
    evalConfigId,
    evalTemplateId,
    organizationId,
    workspaceId,
    mapping,
    expectedDeleted,
  },
) {
  assert(
    audit?.prompt_id === promptId,
    "Prompt eval DB audit prompt id mismatch.",
  );
  assert(
    audit?.config_id === evalConfigId,
    "Prompt eval DB audit config id mismatch.",
  );
  assert(
    audit?.config_prompt_template_id === promptId,
    "Prompt eval DB audit config prompt id mismatch.",
  );
  assert(
    audit?.eval_template_id === evalTemplateId,
    "Prompt eval DB audit eval template id mismatch.",
  );
  assert(
    audit.prompt_organization_id === organizationId,
    "Prompt eval DB audit organization mismatch.",
  );
  if (workspaceId) {
    assert(
      audit.prompt_workspace_id === workspaceId,
      "Prompt eval DB audit workspace mismatch.",
    );
  }
  assert(
    audit.deleted === expectedDeleted,
    "Prompt eval DB audit deleted state mismatch.",
  );
  if (expectedDeleted) {
    assert(
      audit.deleted_at_set === true,
      "Deleted prompt eval config missing deleted_at.",
    );
  }
  assertPromptEvalMapping(audit.mapping, mapping);
}

async function runPostgresJson(sql) {
  const container = process.env.API_JOURNEY_DB_CONTAINER || "ws2-postgres";
  const user = process.env.API_JOURNEY_DB_USER || "user";
  const database = process.env.API_JOURNEY_DB_NAME || "tfc";
  const { stdout } = await execFileAsync(
    "docker",
    ["exec", container, "psql", "-U", user, "-d", database, "-At", "-c", sql],
    { maxBuffer: 10 * 1024 * 1024 },
  );
  const text = stdout.trim();
  assert(text, "Postgres DB audit returned no JSON output.");
  return JSON.parse(text);
}

async function multipartApiRequest({
  apiBase,
  accessToken,
  organizationId,
  workspaceId,
  method,
  pathName,
  fields = {},
  files = [],
}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined || value === null) continue;
    form.append(key, String(value));
  }
  for (const file of files) {
    form.append(
      file.fieldName || "file",
      new Blob([file.content], { type: file.contentType || "text/plain" }),
      file.fileName,
    );
  }

  const response = await fetch(new URL(pathName, apiBase), {
    method,
    headers: {
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(organizationId ? { "X-Organization-Id": organizationId } : {}),
      ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
    },
    body: form,
  });
  const body = await parseApiJourneyResponseBody(response);
  if (!response.ok) {
    const error = new Error(
      `${method} ${pathName} failed with HTTP ${response.status}: ${formatApiJourneyBody(
        body,
      )}`,
    );
    error.status = response.status;
    error.body = body;
    throw error;
  }
  if (body && typeof body === "object" && body.status === false) {
    throw new Error(
      `${method} ${pathName} returned status:false: ${formatApiJourneyBody(body)}`,
    );
  }
  return unwrapApiData(body);
}

async function parseApiJourneyResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function formatApiJourneyBody(body) {
  if (typeof body === "string") return body.slice(0, 1000);
  return JSON.stringify(body).slice(0, 1000);
}

function sqlUuid(value) {
  assert(isUuid(value), "SQL UUID value must be a UUID.");
  return `'${value}'::uuid`;
}

function sqlText(value) {
  assert(typeof value === "string", "SQL text value must be a string.");
  return `'${value.replace(/'/g, "''")}'`;
}

function sqlUuidArray(values) {
  assert(Array.isArray(values), "SQL UUID array value must be an array.");
  for (const value of values) {
    assert(isUuid(value), "SQL UUID array value contained a non-UUID.");
  }
  return `ARRAY[${values.map(sqlUuid).join(", ")}]`;
}

function sqlTextArray(values) {
  assert(Array.isArray(values), "SQL text array value must be an array.");
  return `ARRAY[${values.map(sqlTextLiteral).join(", ")}]`;
}

function sqlTextLiteral(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function sqlJsonLiteral(value) {
  return `${sqlTextLiteral(JSON.stringify(value))}::jsonb`;
}

async function getDatasetTable(client, datasetId, query = {}) {
  return client.get(
    apiPath("/model-hub/develops/{dataset_id}/get-dataset-table/", {
      dataset_id: datasetId,
    }),
    {
      query: {
        page_size: 25,
        current_page_index: 0,
        ...query,
      },
    },
  );
}

async function createOrResolveWritableDataset(
  client,
  cleanup,
  datasetName,
  evidence,
) {
  try {
    const created = await client.post(
      apiPath("/model-hub/develops/create-empty-dataset/"),
      {
        new_dataset_name: datasetName,
        model_type: "generative_llm",
        row: 0,
      },
    );
    const datasetId = created?.dataset_id;
    assert(datasetId, "Create empty dataset did not return dataset_id.");
    cleanup.defer("delete API journey dataset", () =>
      client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
        body: { dataset_ids: [datasetId] },
        okStatuses: [200, 404],
      }),
    );
    evidence.push({ dataset_creation: "created", dataset_id: datasetId });
    return { id: datasetId, source: "created" };
  } catch (error) {
    if (error?.status !== 429) throw error;
    const datasets = asArray(
      await client.get(apiPath("/model-hub/develops/get-datasets/"), {
        query: { page: 0, page_size: 25 },
      }),
    );
    const dataset = datasets.find((row) => row?.id);
    if (!dataset) {
      skip(
        "Dataset creation is plan-limited and no existing dataset is available.",
      );
    }
    evidence.push({
      dataset_creation: "plan-limited; reused existing dataset",
      dataset_id: dataset.id,
    });
    return { id: dataset.id, source: "existing" };
  }
}

async function createExperimentGuardOutsideDataset(
  client,
  cleanup,
  runId,
  evidence,
) {
  const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  const datasetName = `api journey experiment guard ${runId}`;
  const columnName = `api_exp_guard_${suffix}`;
  const cellValue = `outside experiment guard ${runId}`;
  const rowId = randomUUID();

  let datasetId;
  let createdDataset = false;
  try {
    const created = await client.post(
      apiPath("/model-hub/develops/create-empty-dataset/"),
      {
        new_dataset_name: datasetName,
        model_type: "generative_llm",
        row: 0,
      },
    );
    datasetId = created?.dataset_id;
    createdDataset = true;
  } catch (error) {
    if (error?.status !== 429) throw error;
    const datasets = asArray(
      await client.get(apiPath("/model-hub/develops/get-datasets/"), {
        query: { page: 0, page_size: 25 },
      }),
    );
    const fallback = datasets.find((row) => row?.id);
    if (!fallback) {
      skip(
        "Dataset creation is plan-limited and no existing dataset is available.",
      );
    }
    datasetId = fallback.id;
    evidence.push({
      experiment_guard_dataset_creation:
        "plan-limited; reused existing dataset",
      dataset_id: datasetId,
    });
  }
  assert(datasetId, "Create empty dataset did not return dataset_id.");
  if (createdDataset) {
    cleanup.defer("delete API journey experiment guard dataset", () =>
      client.delete(apiPath("/model-hub/develops/delete_dataset/"), {
        body: { dataset_ids: [datasetId] },
        okStatuses: [200, 404],
      }),
    );
  }

  await client.post(
    apiPath("/model-hub/develops/{dataset_id}/add_static_column/", {
      dataset_id: datasetId,
    }),
    {
      new_column_name: columnName,
      column_type: "text",
      source: "OTHERS",
    },
  );

  const table = await getDatasetTable(client, datasetId);
  const column = findColumn(table, columnName);
  assert(column?.id, "Experiment guard outside column was not visible.");
  cleanup.defer("delete API journey experiment guard column", () =>
    ignoreNotFound(() =>
      client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
          dataset_id: datasetId,
          column_id: column.id,
        }),
      ),
    ),
  );
  cleanup.defer("delete API journey experiment guard row", () =>
    client.delete(
      apiPath("/model-hub/develops/{dataset_id}/delete_row/", {
        dataset_id: datasetId,
      }),
      {
        body: { row_ids: [rowId], selected_all_rows: false },
        okStatuses: [200, 404],
      },
    ),
  );

  await client.post(
    apiPath("/model-hub/develops/{dataset_id}/add_rows/", {
      dataset_id: datasetId,
    }),
    {
      rows: [
        {
          id: rowId,
          cells: [{ column_name: columnName, value: cellValue }],
        },
      ],
    },
  );
  evidence.push({
    experiment_guard_dataset_id: datasetId,
    experiment_guard_row_id: rowId,
    experiment_guard_column_id: column.id,
  });
  return {
    dataset_id: datasetId,
    row_id: rowId,
    column_id: column.id,
    cell_value: cellValue,
  };
}

function findColumn(tablePayload, name) {
  return asArray(tablePayload?.column_config).find(
    (column) => column.name === name,
  );
}

function firstDatasetRow(tablePayload) {
  return asArray(tablePayload?.table).find((row) => row?.row_id);
}

function cellValueFor(row, columnId) {
  return row?.[columnId]?.cell_value;
}

function promptConfig(systemText, userText) {
  return {
    messages: [
      { role: "system", content: [{ type: "text", text: systemText }] },
      { role: "user", content: [{ type: "text", text: userText }] },
    ],
    configuration: {
      model: "gpt-4o-mini",
      model_detail: { type: "chat" },
      template_format: "mustache",
    },
    placeholders: [],
  };
}

async function waitForPromptTemplateRunCompletion(
  client,
  { promptId, templateVersion, timeoutMs = 120000, intervalMs = 3000 },
) {
  const deadline = Date.now() + timeoutMs;
  let lastStatus = null;
  while (Date.now() < deadline) {
    lastStatus = await client.get(
      apiPath("/model-hub/prompt-templates/{id}/get-run-status/", {
        id: promptId,
      }),
      { query: { template_version: templateVersion } },
    );
    const result = lastStatus?.executions_result || {};
    const outputRows = payloadArray(result.output, "output");
    const firstOutput = outputRows.find(
      (value) => typeof value === "string" && value.trim().length > 0,
    );
    if (firstOutput) return lastStatus;

    const status = String(lastStatus?.status || "").toLowerCase();
    const errorMessage = lastStatus?.error_message || result?.error_message;
    if (status === "failed" || errorMessage) {
      throw new Error(
        `Prompt Workbench run failed: ${errorMessage || JSON.stringify(lastStatus)}`,
      );
    }
    await sleep(intervalMs);
  }
  throw new Error(
    `Timed out waiting for prompt Workbench run ${promptId}/${templateVersion}; last status=${JSON.stringify(
      lastStatus,
    )}.`,
  );
}

function assertPromptTemplateRunStatus(
  payload,
  { promptId, templateVersion, modelName },
) {
  const result = payload?.executions_result || {};
  assert(
    result.template_version === templateVersion,
    "Prompt Workbench run status returned the wrong version.",
  );
  assert(
    result.original_template === promptId,
    "Prompt Workbench run status returned the wrong prompt id.",
  );
  const outputRows = payloadArray(result.output, "output");
  assert(
    outputRows.some(
      (value) => typeof value === "string" && value.trim().length > 0,
    ),
    "Prompt Workbench run status did not return generated output.",
  );
  const metadataRows = payloadArray(result.metadata, "metadata");
  assert(
    metadataRows.some((row) => row && typeof row === "object"),
    "Prompt Workbench run status did not return run metadata.",
  );
  assert(
    result.prompt_config_snapshot?.configuration?.model === modelName,
    "Prompt Workbench run status returned the wrong model.",
  );
}

async function resolvePromptEvalTemplateForConfig(client, cleanup, suffix) {
  const systemTemplate = await findEvalTemplateDetailByName(
    client,
    "word_count_in_range",
  );
  if (systemTemplate) {
    const paramsConfig = promptEvalParamsForTemplate(systemTemplate);
    return {
      id: systemTemplate.id,
      name: systemTemplate.name,
      source: "system",
      requiredKeys: promptEvalRequiredKeys(systemTemplate),
      ...paramsConfig,
    };
  }

  const name = `api_journey_prompt_eval_${suffix}`;
  const created = await client.post(
    apiPath("/model-hub/eval-templates/create-v2/"),
    {
      name,
      eval_type: "llm",
      instructions:
        "Assess {{output}} against {{expected}} and return Passed or Failed.",
      model: "turing_large",
      output_type: "pass_fail",
      pass_threshold: 0.5,
      description: "Temporary prompt eval template for API journey coverage.",
      tags: ["api-journey", "prompt-eval-config"],
      check_internet: false,
    },
  );
  assert(
    isUuid(created?.id),
    "Fallback prompt eval template create did not return a UUID id.",
  );
  cleanup.defer("delete API journey prompt eval template", () =>
    client.post(apiPath("/model-hub/eval-templates/bulk-delete/"), {
      template_ids: [created.id],
    }),
  );
  const detail = await client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: created.id,
    }),
  );
  return {
    id: created.id,
    name: detail?.name || name,
    source: "created",
    requiredKeys: promptEvalRequiredKeys(detail),
    params: {},
    expectedParams: {},
    schemaKeys: [],
  };
}

async function findEvalTemplateDetailByName(client, name) {
  const payload = await client.post(
    apiPath("/model-hub/eval-templates/list/"),
    {
      page: 0,
      page_size: 10,
      owner_filter: "all",
      search: name,
      sort_by: "updated_at",
      sort_order: "desc",
    },
  );
  const row = payloadArray(payload?.items, "items").find(
    (item) => item?.name === name && isUuid(item?.id),
  );
  if (!row) return null;
  return client.get(
    apiPath("/model-hub/eval-templates/{template_id}/detail/", {
      template_id: row.id,
    }),
  );
}

function promptEvalRequiredKeys(detail) {
  return payloadArray(
    detail?.required_keys ||
      detail?.eval_required_keys ||
      detail?.config?.required_keys,
    "required_keys",
  ).filter((key) => typeof key === "string" && key.length > 0);
}

function promptEvalParamsForTemplate(detail) {
  const schema = detail?.config?.function_params_schema || {};
  if (schema.min_words && schema.max_words) {
    return {
      params: { min_words: "1", max_words: "20" },
      expectedParams: { min_words: 1, max_words: 20 },
      schemaKeys: ["min_words", "max_words"],
    };
  }
  if (schema.k) {
    return {
      params: { k: "3" },
      expectedParams: { k: 3 },
      schemaKeys: ["k"],
    };
  }
  return {
    params: {},
    expectedParams: {},
    schemaKeys: Object.keys(schema),
  };
}

function buildPromptEvalConfigMapping(requiredKeys) {
  const fallbackByKey = {
    expected: "expected",
    expected_output: "expected",
    ground_truth: "expected",
    hypothesis: "text",
    input: "text",
    output: "text",
    reference: "expected",
    response: "text",
    text: "text",
  };
  const mapping = {};
  for (const key of requiredKeys) {
    mapping[key] = fallbackByKey[key] || "text";
  }
  return mapping;
}

function assertPromptEvalMapping(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Prompt eval config mapping did not preserve ${key}.`,
    );
  }
}

function assertPromptEvalRequiredKeys(actual, expected) {
  const actualSet = new Set(payloadArray(actual, "eval_required_keys"));
  for (const key of expected) {
    assert(
      actualSet.has(key),
      `Prompt eval config did not expose required key ${key}.`,
    );
  }
}

function assertPromptEvalParams(actual, expected) {
  for (const [key, value] of Object.entries(expected)) {
    assert(
      actual?.[key] === value,
      `Prompt eval config params did not preserve normalized ${key}.`,
    );
  }
}

function assertPromptEvalSchemaKeys(actual, expectedKeys) {
  for (const key of expectedKeys) {
    assert(
      Object.prototype.hasOwnProperty.call(actual || {}, key),
      `Prompt eval config schema did not expose ${key}.`,
    );
  }
}

async function searchWorkbenchRows(client, name, extraQuery = {}) {
  return asArray(
    await client.get(apiPath("/model-hub/prompt-executions/"), {
      query: {
        send_all: true,
        page: 1,
        page_size: 25,
        name,
        sort_by: "updated_at",
        sort_order: "desc",
        ...extraQuery,
      },
    }),
  );
}

async function findWorkbenchFolderRow(client, name) {
  const rows = await searchWorkbenchRows(client, name);
  return rows.find((row) => row?.type === "FOLDER" && row?.name === name);
}

async function findWorkbenchPromptRow(client, name) {
  const rows = await searchWorkbenchRows(client, name);
  return rows.find((row) => row?.type === "PROMPT" && row?.name === name);
}

async function createWorkbenchPrompt(
  client,
  { folderId, name, runId, description },
) {
  const created = await client.post(
    apiPath("/model-hub/prompt-templates/create-draft/"),
    {
      name,
      description,
      prompt_folder: folderId,
      variable_names: { customer: ["Ada"] },
      metadata: { source: "api-journey", run_id: runId },
      prompt_config: [
        promptConfig(
          "You are a concise assistant.",
          `Hello {{customer}} from ${runId}`,
        ),
      ],
    },
  );
  const promptId =
    created?.id || created?.root_template || created?.rootTemplate;
  assert(isUuid(promptId), "Workbench prompt create did not return a UUID id.");
  return promptId;
}

async function deletePromptTemplateIfPresent(client, promptId) {
  try {
    return await client.post(
      apiPath("/model-hub/prompt-templates/bulk-delete/"),
      {
        ids: [promptId],
      },
    );
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("no valid ids provided for deletion")
    ) {
      return null;
    }
    throw error;
  }
}

async function deletePromptEvalConfigIfPresent(client, promptId, evalConfigId) {
  try {
    return await client.delete(
      apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
        id: promptId,
      }),
      { query: { id: evalConfigId } },
    );
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("does not exist") ||
      message.includes("not found")
    ) {
      return null;
    }
    throw error;
  }
}

function resolveQueueItemSource(item, detail) {
  const detailItem = detail?.item || {};
  const sourceType = detailItem.source_type || item?.source_type;
  const content = detailItem.source_content || item?.source_content || {};
  const sourceId =
    sourceIdFromContent(sourceType, content) ||
    item?.source_id ||
    item?.[`${sourceType}_id`] ||
    item?.[sourceType];
  return { sourceType, sourceId };
}

function sourceIdFromContent(sourceType, content) {
  if (sourceType === "observation_span") {
    return content.span_id || content.id;
  }
  if (sourceType === "trace") {
    return content.trace_id || content.id;
  }
  if (sourceType === "trace_session") {
    return content.session_id || content.id;
  }
  if (sourceType === "dataset_row") {
    return content.row_id || content.id;
  }
  if (sourceType === "call_execution") {
    return content.call_id || content.id;
  }
  return content.id;
}

async function findAnnotationLabelByName(client, name) {
  const rows = asArray(
    await client.get(apiPath("/model-hub/annotations-labels/"), {
      query: { search: name },
    }),
  );
  return rows.find((label) => label.name === name);
}

async function findLegacyAnnotationByName(client, datasetId, name) {
  const rows = asArray(
    await client.get(apiPath("/model-hub/annotations/"), {
      query: { dataset: datasetId, page: 1, limit: 100 },
    }),
  );
  return rows.find((annotation) => annotation.name === name);
}

async function ignoreNotFound(fn) {
  try {
    return await fn();
  } catch (error) {
    const message = String(error?.message || "").toLowerCase();
    if (
      error?.status === 404 ||
      message.includes("not found") ||
      message.includes("not_found") ||
      message.includes("no annotationslabels matches") ||
      message.includes("no annotations matches")
    ) {
      return null;
    }
    throw error;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
