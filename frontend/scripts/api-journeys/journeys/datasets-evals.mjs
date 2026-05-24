import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
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
    id: "DPE-API-014",
    title: "Experiment V2 read surfaces and legacy read compatibility",
    tags: ["experiments", "safe", "db-audit"],
    async run({ client, evidence, organizationId, workspaceId }) {
      const { listRows, row: experimentRow, detail } =
        await selectExperimentForReadCoverage(client);
      assertExperimentListRow(experimentRow);

      const experimentId = experimentRow.id;
      const datasetId = detail.dataset_id || experimentRow.dataset;
      assert(isUuid(experimentId), "Selected experiment id was not a UUID.");
      assert(isUuid(datasetId), "Selected experiment dataset id was not a UUID.");
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
            apiPath(
              "/model-hub/experiments/v2/{experiment_id}/comparisons/",
              { experiment_id: experimentId },
            ),
          ),
          client.get(
            apiPath(
              "/model-hub/experiments/v2/{experiment_id}/json-schema/",
              { experiment_id: experimentId },
            ),
          ),
        ]);
      assertExperimentRowsPayload(rowsPayload, dbAudit);
      assertExperimentColumnOnlyPayload(columnOnlyPayload, dbAudit);
      assertExperimentStatsPayload(stats);
      assertExperimentComparisonsPayload(comparisons, experimentId);
      assert(
        jsonSchema && typeof jsonSchema === "object" && !Array.isArray(jsonSchema),
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
        skip("No eval metric found on selected experiment for eval-stat coverage.");
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
        skip("No experiment feedback candidate found for V2 feedback coverage.");
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
      assert(isUuid(created?.id), "Experiment feedback create did not return id.");
      const feedbackId = created.id;
      cleanup.defer("delete experiment feedback", () =>
        ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId })),
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

      await client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId }));
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
        apiPath("/model-hub/develops/{dataset_id}/add_multiple_static_columns/", {
          dataset_id: datasetId,
        }),
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
          apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
            dataset_id: datasetId,
            column_id: columnId,
          }),
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
        deleted_cells_with_deleted_at: deletedAudit.deleted_cell_deleted_at_count,
        stale_column_config_entries: deletedAudit.column_config_contains_deleted_count,
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
        Number(afterAudit.user_key_count) === Number(beforeAudit.user_key_count),
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
    async run({ client, cleanup, runId, user, evidence, organizationId, workspaceId }) {
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
      assert(staticColumn?.id, "Temporary static annotation column was not visible.");
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
      assert(row?.row_id, "No dataset row was available for annotation coverage.");
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

      const annotationTasks = asArray(
        await client.get(apiPath("/model-hub/annotation-tasks/"), {
          query: { page: 1, limit: 10 },
        }),
      );
      annotationTaskListCount = annotationTasks.length;
      const annotationTask = annotationTasks.find((task) => task?.id);
      if (annotationTask?.id) {
        await client.get(
          apiPath("/model-hub/annotation-tasks/{id}/", {
            id: annotationTask.id,
          }),
        );
        annotationTaskReadStatus = 200;
      }

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
        listedAnnotations.some((annotation) => annotation.id === detailAnnotation.id),
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
      assert(labelCell?.column_id, "annotate_row did not expose label column id.");
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
      const updatedLabelCell = asArray((afterUpdate?.data || afterUpdate)?.label).find(
        (label) => label.label_id === labelId,
      );
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
        asArray(patched.assigned_users).some((assigned) => assigned.id === userId),
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
      assert(bulkDeletedCount === 1, "bulk_destroy did not delete one annotation.");
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
        annotation_task_read_status: annotationTaskReadStatus,
        required_update_status: requiredUpdateStatus,
        bulk_deleted_count: bulkDeletedCount,
        deleted_annotation_ids: Array.from(deletedAnnotationIds),
        generated_column_count: Number(audit.generated_column_count),
        generated_cell_count: Number(audit.generated_cell_count),
        active_annotation_count: Number(audit.active_annotation_count),
        active_generated_column_count: Number(audit.active_generated_column_count),
        active_generated_cell_count: Number(audit.active_generated_cell_count),
      });
    },
  },
  {
    id: "PROMPT-API-002",
    title:
      "Dataset run-prompt preview, create, config reload, row rerun, and guards",
    tags: ["prompts", "dataset", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      let table = await getDatasetTable(client, datasetId, {
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

      await client.post(
        apiPath("/model-hub/develops/add_run_prompt_column/"),
        {
          dataset_id: datasetId,
          name: outputName,
          config,
        },
      );

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
      const createResult = await client.get(
        apiPath("/model-hub/knowledge-base/"),
        { query: { type: "create", name: `api journey kb ${runId}` } },
      );
      const updateResult = await client.get(
        apiPath("/model-hub/knowledge-base/"),
        { query: { type: "update", name: `api journey kb ${runId}` } },
      );
      const afterAudit = await loadUserSdkCredentialDbAudit(
        organizationId,
        email,
      );

      assertKnowledgeBaseSdkSnippetSafety(createResult?.code, "create");
      assertKnowledgeBaseSdkSnippetSafety(updateResult?.code, "update");
      assert(
        Number(afterAudit.user_key_count) === Number(beforeAudit.user_key_count),
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
      assert(isUuid(kbId), "Knowledge base create did not return a UUID kb_id.");
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
    async run({ client, cleanup, evidence, organizationId, workspaceId, runId }) {
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

      const created = await client.post(apiPath("/model-hub/kb/"), {
        name,
        embedding_model: embeddingModel,
        chunk_size: 256,
      });
      kbId = created?.id;
      assert(isUuid(kbId), "Structured KB create did not return a UUID id.");
      cleanup.defer("delete API journey structured knowledge base", async () => {
        if (!kbId || deleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/kb/{id}/", { id: kbId })),
        );
      });
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

      const detail = await client.get(apiPath("/model-hub/kb/{id}/", { id: kbId }));
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
        frontend_usage: "No current frontend caller found; UI uses legacy endpoints.knowledge.*",
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      assert(isUuid(templateId), "Eval template create did not return a UUID id.");
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
      assert(isUuid(version?.id), "Eval version create did not return a UUID id.");
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
      assert(isUuid(restored?.id), "Eval version restore did not return a UUID id.");
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
        Number(afterAudit.user_key_count) === Number(beforeAudit.user_key_count),
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
      assert(isUuid(templateId), "Eval summary template create did not return id.");
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
        initial_template_count: payloadArray(
          beforeList?.templates,
          "templates",
        ).length,
        created_list_contains_template: true,
        db_row_removed_after_delete: postDeleteAudit.row_exists === false,
      });
    },
  },
  {
    id: "EVAL-API-013",
    title: "Eval playground execute, feedback, and log round trip",
    tags: ["evals", "eval-playground", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId, user }) {
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
          client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId })),
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

      const execution = await client.post(apiPath("/model-hub/eval-playground/"), {
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
      });
      assertEvalPlaygroundExecutionPayload(execution);
      logId = execution.log_id;

      const logDetail = await client.get(apiPath("/model-hub/get-eval-logs"), {
        query: { log_id: logId },
      });
      assertEvalPlaygroundLogDetail(logDetail, logId);

      const evalConfig = await client.get(apiPath("/model-hub/get-eval-config"), {
        query: { eval_id: templateId },
      });
      assertEvalPlaygroundConfigPayload(evalConfig, { templateId, templateName });

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
            sort: JSON.stringify([{ column_id: "column1", type: "descending" }]),
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
      assert(isUuid(feedback?.feedback_id), "Feedback create did not return id.");
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
      assertEvalPlaygroundFeedbackList(feedbackList, feedbackId, updatedFeedback);

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

      await client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId }));
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
          client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId })),
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

      const execution = await client.post(apiPath("/model-hub/eval-playground/"), {
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
      });
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
      assert(isUuid(feedback?.feedback_id), "Recalculate feedback did not return id.");
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

      await client.delete(apiPath("/model-hub/feedback/{id}/", { id: feedbackId }));
      feedbackDeleted = true;
      await client.delete(apiPath("/model-hub/get-eval-logs"), {
        body: { log_ids: [logId] },
      });
      logDeleted = true;
      const deletedLocalizerAudit = await loadEvalPlaygroundErrorLocalizerAudit({
        templateId,
        logId,
        organizationId,
        workspaceId,
      });
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
    tags: ["evals", "test-evaluation", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const templateName = `api_journey_test_eval_${suffix}`;

      const template = await findEvalTemplateDetailByName(
        client,
        "word_count_in_range",
      );
      if (!template) {
        skip("System eval word_count_in_range was not available for test-evaluation.");
      }
      const templateId = template.id;
      const evalTypeId =
        template?.config?.eval_type_id || template?.eval_type_id || template.name;
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      const childOneVersion = await firstEvalTemplateVersion(client, childOne.id);
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      let outputColumnDeleted = false;
      let expectedColumnDeleted = false;

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
      assert(outputColumn?.id, "Composite binding output column was not visible.");
      assert(
        expectedColumn?.id,
        "Composite binding expected column was not visible.",
      );
      cleanup.defer("delete composite binding expected column", async () => {
        if (expectedColumnDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
              dataset_id: datasetId,
              column_id: expectedColumn.id,
            }),
          ),
        );
      });
      cleanup.defer("delete composite binding output column", async () => {
        if (outputColumnDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_column/{column_id}/", {
              dataset_id: datasetId,
              column_id: outputColumn.id,
            }),
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
      assert(isUuid(metricId), "Composite dataset binding DB audit found no metric.");
      cleanup.defer("delete API journey composite dataset metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
              dataset_id: datasetId,
              eval_id: metricId,
            }),
            { body: { delete_column: true }, okStatuses: [200, 404] },
          ),
        );
      });

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
          dataset_id: datasetId,
          eval_id: metricId,
        }),
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      cleanup.defer("delete API journey experiment binding composite eval", () =>
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
      cleanup.defer("delete API journey composite experiment metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
              dataset_id: experiment.dataset_id,
              eval_id: metricId,
            }),
            {
              body: { delete_column: true, experiment_id: experiment.id },
              okStatuses: [200, 400, 404],
            },
          ),
        );
      });

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
        apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
          dataset_id: deleteDatasetId,
          eval_id: metricId,
        }),
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
    tags: ["evals", "legacy-eval-groups", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
        skip("At least two eval templates are required for eval group coverage.");
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
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
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
        relationship_count_after_delete: Number(deletedAudit.relationship_count),
      });
    },
  },
  {
    id: "EVAL-API-007",
    title: "Apply eval group to dataset and delete generated metric",
    tags: ["evals", "dataset", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
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
      assert(isUuid(metricId), "Dataset apply DB audit did not return metric id.");
      cleanup.defer("delete API journey dataset eval metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
              dataset_id: dataset.id,
              eval_id: metricId,
            }),
            { body: { delete_column: true }, okStatuses: [200, 404] },
          ),
        );
      });

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
          dataset_id: dataset.id,
          eval_id: metricId,
        }),
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

      await client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }));
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
          payloadArray(deletedMetricAudit.metrics, "metrics")[0]?.deleted_at_set ===
          true,
      });
    },
  },
  {
    id: "EVAL-API-008",
    title: "Apply eval group to prompt template and delete generated config",
    tags: ["evals", "prompts", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
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
      assertPromptApplyPayload(appliedConfig, { templateId: template.id, mapping });

      const configId = appliedConfig.id;
      cleanup.defer("delete API journey prompt eval config", async () => {
        if (configDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            `${apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
              id: prompt.id,
            })}?id=${encodeURIComponent(configId)}`,
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
        `${apiPath("/model-hub/prompt-templates/{id}/delete-evaluation-config/", {
          id: prompt.id,
        })}?id=${encodeURIComponent(configId)}`,
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

      await client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }));
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_sim_apply_group_${suffix}`;
      const description = "Eval group simulation fanout created by API journey.";

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
      cleanup.defer("delete API journey simulate apply eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
        );
      });

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
        apiPath("/simulate/run-tests/{run_test_id}/eval-configs/{eval_config_id}/", {
          run_test_id: runTest.id,
          eval_config_id: configId,
        }),
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

      await client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }));
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();
      const suffix = runId.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      const groupName = `api_journey_experiment_apply_group_${suffix}`;
      const description = "Eval group experiment fanout created by API journey.";

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
      cleanup.defer("delete API journey experiment apply eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
        );
      });

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
      assert(isUuid(metricId), "Experiment apply DB audit did not return metric id.");
      cleanup.defer("delete API journey experiment eval metric", async () => {
        if (metricDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
              dataset_id: experiment.dataset_id,
              eval_id: metricId,
            }),
            {
              body: { delete_column: true, experiment_id: experiment.id },
              okStatuses: [200, 400, 404],
            },
          ),
        );
      });

      await client.delete(
        apiPath("/model-hub/develops/{dataset_id}/delete_user_eval/{eval_id}/", {
          dataset_id: experiment.dataset_id,
          eval_id: metricId,
        }),
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

      await client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }));
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
        existing_active_eval_metrics: Number(experiment.active_eval_metric_count),
        active_metric_count_after_apply: Number(appliedAudit.active_count),
        deleted_metric_count_after_cleanup: Number(
          deletedMetricAudit.deleted_count,
        ),
        experiment_active_metrics_after_cleanup: Number(
          deletedMetricAudit.experiment_active_metric_count,
        ),
        deleted_at_set_after_cleanup:
          payloadArray(deletedMetricAudit.metrics, "metrics")[0]?.deleted_at_set ===
          true,
      });
    },
  },
  {
    id: "EVAL-API-011",
    title: "Apply eval group to eval task and delete generated custom config",
    tags: ["evals", "observe", "eval-task", "mutating", "data-roundtrip", "db-audit"],
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      cleanup.defer("delete API journey eval-task apply eval group", async () => {
        if (groupDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId })),
        );
      });

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
      cleanup.defer("delete API journey eval-task custom eval config", async () => {
        if (configDeleted) return null;
        return ignoreNotFound(() =>
          client.delete(
            apiPath("/tracer/custom-eval-config/{id}/", {
              id: configId,
            }),
            { okStatuses: [200, 204, 400, 404] },
          ),
        );
      });

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

      await client.delete(apiPath("/model-hub/eval-groups/{id}/", { id: groupId }));
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
        existing_active_custom_eval_configs: Number(project.active_config_count),
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
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
      assert(isUuid(folderId), "Prompt folder create did not return a UUID id.");
      cleanup.defer("delete API journey prompt folder", async () => {
        if (!folderDeleted) {
          await ignoreNotFound(() =>
            client.delete(apiPath("/model-hub/prompt-folders/{id}/", { id: folderId })),
          );
        }
      });

      const folderDetail = await client.get(
        apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }),
      );
      assert(folderDetail?.name === folderName, "Prompt folder detail name mismatch.");

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
        JSON.stringify(promptDetail?.prompt_config || []).includes("{{customer}}"),
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

      await client.delete(apiPath("/model-hub/prompt-folders/{id}/", { id: folderId }));
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
        prompt_versions_deleted: Number(deletedPromptAudit.deleted_version_count),
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
    async run({ client, cleanup, runId, evidence, organizationId, workspaceId }) {
      requireMutations();
      const suffix = runId.replace(/[^a-z0-9]/gi, "").toLowerCase().slice(0, 18);
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
      assert(isUuid(promptId), "Prompt eval journey create did not return a UUID id.");
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
      assert(configRow, "Evaluation configs did not include the created config.");
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
      assertPromptEvalRequiredKeys(configRow.eval_required_keys, evalTemplate.requiredKeys);
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
        (Array.isArray(textVariable)
          ? textVariable[0]
          : textVariable) === "one two three four",
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
        apiPath(
          "/model-hub/prompt-templates/{id}/delete-evaluation-config/",
          { id: promptId },
        ),
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
    assert(isUuid(candidate[key]), `Experiment feedback candidate missing ${key}.`);
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
  const auditMetrics = payloadArray(dbAudit?.eval_metric_ids, "eval_metric_ids");
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
  assert(payload?.id === templateId, "Eval summary template returned wrong id.");
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
    JSON.stringify(memberIds) === JSON.stringify([...expectedTemplateIds].sort()),
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
  assert(audit.row_exists === true, "Eval group DB audit did not find group row.");
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
    JSON.stringify(relationIds) === JSON.stringify([...expectedTemplateIds].sort()),
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
  if (!datasets.length) skip("No datasets found for eval group apply coverage.");

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
    const columnsByName = new Map(columns.map((column) => [column.name, column]));
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
  assert(detail?.id === runTest.id, "Simulation run-test detail returned wrong id.");
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

  skip("No experiment candidate could be mapped to composite output/expected keys.");
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

function assertEvalTaskApplyPayload(payload, { templateId, projectId, mapping }) {
  assert(
    payload?.eval_template === templateId,
    "Eval-task apply returned wrong eval template id.",
  );
  assert(payload.project === projectId, "Eval-task apply returned wrong project id.");
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
  assert(audit.group_id === groupId, "Dataset apply DB audit group id mismatch.");
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
  assert(metric?.template_id === templateId, "Dataset apply used wrong template.");
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
    assert(metric.status === "Inactive", "Dataset apply metric was not inactive.");
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
  assert(audit.group_id === groupId, "Prompt apply DB audit group id mismatch.");
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
  assert(audit.group_id === groupId, "Simulate apply DB audit group id mismatch.");
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
  assert(config?.id === configId, "Simulate apply DB audit config id mismatch.");
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
  assert(metric?.template_id === templateId, "Experiment apply used wrong template.");
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
  assert(config?.id === configId, "Eval-task apply DB audit config id mismatch.");
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
  assert(detail.owner === "user", "Eval template detail did not mark owner=user.");
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
      description: "Composite child with required keys for API journey regression.",
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
      description: "Eval playground template created by API journey regression.",
      tags: ["api-journey", "eval-playground"],
    },
  );
  assert(
    isUuid(created?.id),
    "Eval playground eval create did not return a UUID id.",
  );
  return created;
}

function testEvaluationPayload({ name, templateId, evalTypeId, mapping, params }) {
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
  return String(value || "").trim().toLowerCase();
}

function assertEvalPlaygroundExecutionPayload(payload) {
  assert(payload && typeof payload === "object", "Eval playground returned no payload.");
  assert(
    Object.prototype.hasOwnProperty.call(payload, "output"),
    "Eval playground response did not include output.",
  );
  assert(isUuid(payload.log_id), "Eval playground response did not include log_id.");
}

function assertEvalPlaygroundLogDetail(payload, logId) {
  assert(payload && typeof payload === "object", "Eval log detail returned no payload.");
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
    String(payload?.log_id) === logId || String(payload?.evaluation_id) === logId,
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

function assertEvalPlaygroundConfigPayload(payload, { templateId, templateName }) {
  const config = payload?.eval || payload;
  assert(config && typeof config === "object", "Eval config returned no payload.");
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
    (row) =>
      row?.id === templateId || row?.eval_template_name === templateName,
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
  { templateId, evalTypeId, logIds, organizationId, workspaceId, expectedLogsDeleted },
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
      !audit.template_workspace_id || audit.template_workspace_id === workspaceId,
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
  assert(audit?.template_id === templateId, "Playground DB audit returned wrong template.");
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
    assert(audit.template_deleted === true, "Playground template was not deleted.");
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

  assert(audit.template_deleted === false, "Playground template was deleted early.");
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
  assert(audit?.setting_exists === true, "Eval log settings audit found no row.");
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

function assertCompositeExecutePayload(payload, { compositeId, name, childIds }) {
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
  assert(Array.isArray(chart.chart), "Eval list-charts chart was not an array.");
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
    payload.columns?.includes("question") && payload.columns?.includes("answer"),
    "Ground-truth data did not return uploaded columns.",
  );
}

function assertGroundTruthStatusPayload(payload, groundTruthId) {
  assert(payload?.id === groundTruthId, "Ground-truth status returned wrong id.");
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
  assert(
    audit.owner === "user",
    "Eval DB audit did not persist owner=user.",
  );
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
  assert(metric?.template_id === templateId, "Composite binding used wrong template.");
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
    assert(metric.status === "Inactive", "Composite binding metric was not inactive.");
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
  assert(config?.dataset_id === datasetId, "Run-prompt config dataset mismatch.");
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
    const row = asArray(table?.table).find((candidate) => candidate.row_id === rowId);
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
  { datasetId, organizationId, workspaceId, expectedDeleted, rowCount, columnCount },
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
  assert(audit.column_name === outputName, "Run-prompt DB column name mismatch.");
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
    assert(audit.column_deleted === true, "Run-prompt output column not deleted.");
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
    assert(audit.column_deleted === false, "Run-prompt output column is deleted.");
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
  assert(row.name === kbName, "Knowledge base table returned the wrong KB name.");
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
  assert(row, "Knowledge base files endpoint did not return the uploaded file.");
  assert(row.name === fileName, "Knowledge base files endpoint returned wrong name.");
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
  assert(audit?.kb_id === kbId, "Knowledge base DB audit returned wrong KB id.");
  assert(audit.name === kbName, "Knowledge base DB audit returned wrong KB name.");
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
  assert(Array.isArray(models), "Structured KB embedding model list was not an array.");
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
  assert(payload.name === name, "Structured KB payload returned the wrong name.");
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

async function loadKnowledgeBaseOutsideFileCandidate({
  kbId,
  organizationId,
}) {
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
  assert(
    isUuid(experimentId),
    "experimentId must be a UUID for DB audit.",
  );
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
  assert(isUuid(organizationId), "organizationId must be a UUID for localizer audit.");
  if (workspaceId)
    assert(isUuid(workspaceId), "workspaceId must be a UUID for localizer audit.");
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

async function loadCompositeEvalDbAudit(compositeId, organizationId, workspaceId) {
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

async function loadExperimentApplyCandidatesDbAudit(organizationId, workspaceId) {
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
  assert(audit?.prompt_id === promptId, "Prompt eval DB audit prompt id mismatch.");
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
    throw new Error(
      `${method} ${pathName} failed with HTTP ${response.status}: ${formatApiJourneyBody(
        body,
      )}`,
    );
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
  const payload = await client.post(apiPath("/model-hub/eval-templates/list/"), {
    page: 0,
    page_size: 10,
    owner_filter: "all",
    search: name,
    sort_by: "updated_at",
    sort_order: "desc",
  });
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
  const promptId = created?.id || created?.root_template || created?.rootTemplate;
  assert(isUuid(promptId), "Workbench prompt create did not return a UUID id.");
  return promptId;
}

async function deletePromptTemplateIfPresent(client, promptId) {
  try {
    return await client.post(apiPath("/model-hub/prompt-templates/bulk-delete/"), {
      ids: [promptId],
    });
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
      apiPath(
        "/model-hub/prompt-templates/{id}/delete-evaluation-config/",
        { id: promptId },
      ),
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
