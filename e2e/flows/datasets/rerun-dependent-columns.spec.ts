import { test, expect } from "../../lib/fixtures";
import { flowAnnotation } from "../../lib/flow-meta";
import { POLL } from "../../lib/state-probe";

// Pinned from ManualDatasetCreateRequestSerializer and
// ManuallyCreateDatasetView in futureagi/model_hub/views/develop_dataset.py.
const CREATE_DATASET_PATH = "/model-hub/develops/create-dataset-manually/";
// Pinned from DatasetUpdateCellValueRequestSerializer.
const updateCellPath = (datasetId: string) =>
  `/model-hub/develops/${datasetId}/update_cell_value/`;
// Pinned from ClassifyColumnRequestSerializer and ClassifyColumnView.
const classifyColumnPath = (datasetId: string) =>
  `/model-hub/datasets/${datasetId}/classify-column/`;
// Pinned from frontend/src/utils/axios.js `getDatasetDetail`.
const datasetTablePath = (datasetId: string) =>
  `/model-hub/develops/${datasetId}/get-dataset-table/`;
// Pinned from frontend/src/utils/axios.js `updateDynamicColumn`.
const RERUN_PATH = /\/model-hub\/columns\/([^/]+)\/rerun-operation\/$/;
const UI_READY = 60_000;
const RERUN_CHAIN_READY = 240_000;

interface DatasetTableColumn {
  id: string;
  name: string;
  origin_type: string | null;
  status: string | null;
}

interface DatasetTableRow {
  row_id: string;
}

interface DatasetTableEnvelope {
  status: boolean;
  result: {
    column_config: DatasetTableColumn[];
    table: DatasetTableRow[];
  };
}

interface CreatedDatasetEnvelope {
  status: boolean;
  result: {
    dataset_id: string;
  };
}

interface CreatedColumnEnvelope {
  status: boolean;
  result: {
    new_column_id: string;
  };
}

test(
  "DATA-E2E-001: editing a dynamic column reruns selected dependents in order",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "DATA-E2E-001",
      area: "datasets",
      userGoal:
        "A dataset editor keeps derived columns current after changing an upstream dynamic column",
      steps: [
        "seed a dataset with a three-level classification dependency chain",
        "open the source classification column configuration",
        "update the source column",
        "review the direct and transitive dependents in the confirmation dialog",
        "rerun the selected dependent columns",
      ],
      backendChecks: [
        "the browser posts dependent reruns in topological order with the stored operation type",
        "the source and both selected dependent columns reach Completed",
      ],
    }),
  },
  async ({ page, actor }, testInfo) => {
    // Three seed jobs (3 x ASYNC_JOB) + the source/dependent chain
    // (RERUN_CHAIN_READY) + five UI/API waits (5 x 60s) = 720s.
    test.setTimeout(720_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const datasetName = `e2e-data1-${suffix}`;
    const sourceName = `e2e-source-${suffix}`;
    const directName = `e2e-direct-${suffix}`;
    const transitiveName = `e2e-transitive-${suffix}`;

    const createdDataset = await actor.api.post<CreatedDatasetEnvelope>(
      CREATE_DATASET_PATH,
      {
        dataset_name: datasetName,
        number_of_rows: 1,
        number_of_columns: 1,
      },
    );
    const datasetId = createdDataset.result.dataset_id;

    const readDataset = () =>
      actor.api.get<DatasetTableEnvelope>(datasetTablePath(datasetId), {
        current_page_index: 0,
        filters: "[]",
        sort: "[]",
        page_size: 30,
      });

    const initialDataset = await readDataset();
    const staticColumnId = initialDataset.result.column_config[0].id;
    const rowId = initialDataset.result.table[0].row_id;

    await actor.api.post(updateCellPath(datasetId), {
      row_id: rowId,
      column_id: staticColumnId,
      new_value: `seed-${suffix}`,
    });

    const waitForCompleted = async (columnId: string) => {
      await expect
        .poll(async () => {
          const dataset = await readDataset();
          return dataset.result.column_config.find(
            (column) => column.id === columnId,
          )?.status;
        }, POLL.ASYNC_JOB)
        .toBe("Completed");
    };

    const createClassification = async (
      columnId: string,
      newColumnName: string,
    ) => {
      const created = await actor.api.post<CreatedColumnEnvelope>(
        classifyColumnPath(datasetId),
        {
          column_id: columnId,
          labels: ["alpha", "beta"],
          language_model_id: "gpt-4o",
          concurrency: 1,
          new_column_name: newColumnName,
        },
      );
      await waitForCompleted(created.result.new_column_id);
      return created.result.new_column_id;
    };

    const sourceColumnId = await createClassification(
      staticColumnId,
      sourceName,
    );
    const directColumnId = await createClassification(
      sourceColumnId,
      directName,
    );
    const transitiveColumnId = await createClassification(
      directColumnId,
      transitiveName,
    );

    await testInfo.attach("seeded-dataset", {
      body: JSON.stringify({
        datasetId,
        rowId,
        staticColumnId,
        sourceColumnId,
        directColumnId,
        transitiveColumnId,
      }),
      contentType: "application/json",
    });

    const rerunRequests: Array<{
      columnId: string;
      operationType: string | undefined;
    }> = [];
    page.on("request", (request) => {
      if (request.method() !== "POST") return;
      const match = new URL(request.url()).pathname.match(RERUN_PATH);
      if (!match) return;

      let operationType: string | undefined;
      try {
        operationType = request.postDataJSON()?.operation_type;
      } catch {
        operationType = undefined;
      }
      rerunRequests.push({ columnId: match[1], operationType });
    });

    await test.step("UI: update the source classification column", async () => {
      await page.goto(`/dashboard/develop/${datasetId}?tab=data`, {
        waitUntil: "domcontentloaded",
      });

      const sourceHeader = page
        .locator(".ag-header-cell")
        .filter({ hasText: sourceName });
      await expect(sourceHeader).toBeVisible({ timeout: UI_READY });
      await sourceHeader.getByRole("button").click();
      await page.getByText("Configure Dynamic Column", { exact: true }).click();

      await expect(
        page.getByText("Edit Classification", { exact: true }),
      ).toBeVisible({ timeout: UI_READY });
      await page.getByRole("button", { name: "Update Column" }).click();
    });

    await test.step("UI: confirm the direct and transitive reruns", async () => {
      const dialog = page.getByRole("dialog", {
        name: "Rerun dependent columns?",
      });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await expect(
        dialog.getByRole("checkbox", { name: directName }),
      ).toBeChecked();
      await expect(
        dialog.getByRole("checkbox", { name: transitiveName }),
      ).toBeChecked();

      await dialog.getByRole("button", { name: "Rerun selected" }).click();
      await expect(dialog).toBeHidden({ timeout: RERUN_CHAIN_READY });
    });

    await test.step("API: dependents rerun in topological order", async () => {
      await expect
        .poll(
          () =>
            rerunRequests.filter(
              (request) => request.columnId !== sourceColumnId,
            ),
          POLL.ASYNC_JOB,
        )
        .toEqual([
          { columnId: directColumnId, operationType: "classify" },
          { columnId: transitiveColumnId, operationType: "classify" },
        ]);
    });

    await test.step("API: the rerun chain reaches Completed", async () => {
      const expectedIds = [
        sourceColumnId,
        directColumnId,
        transitiveColumnId,
      ].sort();
      await expect
        .poll(async () => {
          const dataset = await readDataset();
          return dataset.result.column_config
            .filter((column) => expectedIds.includes(column.id))
            .filter((column) => column.status === "Completed")
            .map((column) => column.id)
            .sort();
        }, POLL.ASYNC_JOB)
        .toEqual(expectedIds);
    });
  },
);
