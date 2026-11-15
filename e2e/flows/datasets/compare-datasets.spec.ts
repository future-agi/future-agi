import type {
  APIResponse,
  Page,
  Request,
  Response,
  Route,
  TestInfo,
} from "@playwright/test";
import { randomUUID } from "node:crypto";
import { test, expect } from "../../lib/fixtures";
import type { TestActor } from "../../lib/provisioning";
import { POLL, type StateProbe } from "../../lib/state-probe";
import { flowAnnotation } from "../../lib/flow-meta";
import { observeCompareGrid } from "../../lib/compare-grid-probe";

// UploadFileModal.jsx / CreateDatasetFromLocalFileRequestSerializer.
const UPLOAD = "/model-hub/develops/create-dataset-from-local-file/";
// src/utils/axios.js endpoints.dataset.getCompareDataset / CompareDatasetsView.
const comparePath = (id: string) =>
  `/model-hub/datasets/${id}/compare-datasets/`;
// CompareData.jsx uses ten-row server-side blocks and 120px ordinary rows.
const PAGE_SIZE = 10;
const ROW_HEIGHT = 120;
const UI_READY = 60_000;
// Eleven shared inputs: the second block holds exactly one row, the edge where
// CompareDatasetsView's page writer once repeated rows 0-9 as the last block.
const SHARED = 11;
// src/utils/axios.js endpoints.dataset.getCompareDatasetRow / GetCompareDatasetRow.
const rowPath = (compareId: string, rowId: string) =>
  `/model-hub/datasets/get-compare-row/${compareId}/${rowId}/`;

type Column = { id: string; name: string };
type SourceRow = { row_id: string; input: string; output: string };
type Dataset = {
  id: string;
  name: string;
  columns: Column[];
  rows: SourceRow[];
};
// model_hub.utils.utils.get_diff words, as GenerateDiffText renders them.
type DiffWord = { text: string; status: "default" | "removed" | "added" };
type CompareCell = {
  cell_value: string;
  cell_row_id: string;
  cell_diff_value?: DiffWord[] | null;
};
// CompareDatasetResponseSerializer and the actual CompareData datasource envelope.
type CompareRow = Record<string, string | CompareCell>;
type ComparePage = {
  status: boolean;
  result: {
    column_config: Column[];
    table: CompareRow[];
    metadata: { compare_id: string; total_rows: number; total_pages: number };
  };
};
// GetCompareDatasetRow response, as CompareDatasetDetailDrawer reads it.
type RowDetail = {
  status: boolean;
  result: {
    table: CompareRow[];
    prev_row_id: string | null;
    next_row_id: string | null;
  };
};
const cellValue = (row: CompareRow, columnId: string) =>
  (row[columnId] as CompareCell).cell_value;

test(
  "DATA-E2E-005: compared datasets show joined rows across both result blocks",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "DATA-E2E-005",
      area: "datasets",
      userGoal:
        "Compare two uploaded datasets and inspect every matching input with both outputs across result blocks and in row detail, including the word diff",
      steps: [
        "upload two synthetic CSV datasets with eleven shared inputs and one unmatched input each",
        "open Compare from the first dataset, select the second and join on input",
        "open Data and read the first ten joined input and output pairs",
        "scroll to the final joined row, alone in the second block, and verify that unmatched inputs are absent",
        "click the final joined row, read both outputs in row detail and step back across the block boundary with Prev",
        "close row detail, click the first joined row and confirm Prev is unavailable",
        "switch the second output to Difference and read the first output removed and the second added",
      ],
      backendChecks: [
        "both uploaded datasets retain the actor scope and exact independently seeded input/output pairs in Postgres",
        "the browser comparison and API replay return exactly eleven joined pairs across ten-row blocks with one stable compare_id",
        "row detail requests return the clicked joined row with previous and next row ids that cross the block boundary and stop at the first and last rows",
        "the row detail cell of the second output carries the planted word diff against the first output, and the first output carries none",
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Two uploads: 2 * (UI_READY + ASYNC_JOB + UI_READY) = 360s.
    // Compare navigation + two result blocks: 3 * UI_READY = 180s.
    // Row detail: last row, Prev, first row, Difference: 4 * UI_READY = 240s;
    // 60s headroom.
    test.setTimeout(840_000);
    page.setDefaultTimeout(UI_READY);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const expected = Array.from({ length: SHARED }, (_, index) => {
      const key = `${suffix}-${String(index).padStart(2, "0")}`;
      return {
        input: `join-${key}`,
        left: `left-${key}`,
        right: `right-${key}`,
      };
    });
    const datasets: Dataset[] = [];

    for (const side of ["left", "right"] as const) {
      const name = `e2e-data3-${side}-${suffix}`;
      const seeded = [
        ...expected.map((row) => ({ input: row.input, output: row[side] })),
        {
          input: `${side}-only-${suffix}`,
          output: `unmatched-${side}-${suffix}`,
        },
      ];
      let datasetId = "";
      await test.step(
        `upload ${side} dataset through the native CSV form`,
        async () => {
          await page.goto("/dashboard/develop", {
            waitUntil: "domcontentloaded",
          });
          await page
            .getByRole("button", { name: /^add dataset$/i })
            .first()
            .click();
          await page
            .getByText("Upload a file (JSON, CSV)", { exact: true })
            .click();
          const dialog = page.getByRole("dialog").filter({
            has: page.getByText("Upload a File", { exact: true }),
          });
          // Same synthetic CSV/file-input shape as lib/upload.ts; unique values make
          // this flow independent of prior uploads in the worker-scoped actor.
          const csv = [
            "input,output",
            ...seeded.map((row) => `${row.input},${row.output}`),
          ].join("\n");
          await dialog.locator('input[type="file"]').setInputFiles({
            name: `compare-${side}.csv`,
            mimeType: "text/csv",
            buffer: Buffer.from(`${csv}\n`),
          });
          await dialog.getByPlaceholder("Dataset Name").fill(name);
          const uploaded = page.waitForResponse(
            (response) =>
              new URL(response.url()).pathname === UPLOAD &&
              response.request().method() === "POST",
            { timeout: UI_READY },
          );
          await dialog
            .getByRole("button", { name: "Upload", exact: true })
            .click();
          const response = await uploaded;
          expect(response.status()).toBe(200);
          datasetId = (await response.json()).result.dataset_id;
          await testInfo.attach(`uploaded-${side}`, {
            contentType: "application/json",
            body: JSON.stringify({
              datasetId,
              name,
              seeded,
              organizationId: actor.organizationId,
              workspaceId: actor.workspaceId,
            }),
          });
        },
        { timeout: UI_READY },
      );

      await test.step(`backend check 1: ${side} scope and exact imported source rows`, async () => {
        // CSV import is an asynchronous job; CompareDatasetsView reads these PG
        // columns/cells directly, so a CDC wait is neither necessary nor evidence.
        await expect
          .poll(
            async () =>
              (
                await probe.pg<{ n: string }>(
                  "SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted",
                  [datasetId],
                )
              )[0].n,
            POLL.ASYNC_JOB,
          )
          .toBe(String((SHARED + 1) * 2));
        expect(
          await probe.pg<{
            name: string;
            organization_id: string;
            workspace_id: string;
          }>(
            "SELECT name, organization_id, workspace_id FROM model_hub_dataset WHERE id = $1 AND NOT deleted",
            [datasetId],
          ),
        ).toEqual([
          {
            name,
            organization_id: actor.organizationId,
            workspace_id: actor.workspaceId,
          },
        ]);
        const columns = await probe.pg<Column>(
          "SELECT id, name FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name",
          [datasetId],
        );
        expect(columns.map((column) => column.name)).toEqual([
          "input",
          "output",
        ]);
        const rows = await probe.pg<SourceRow>(
          `SELECT i.row_id, i.value AS input, o.value AS output
         FROM model_hub_cell i JOIN model_hub_cell o
           ON o.dataset_id = i.dataset_id AND o.row_id = i.row_id
         WHERE i.dataset_id = $1 AND i.column_id = $2 AND o.column_id = $3
           AND NOT i.deleted AND NOT o.deleted ORDER BY i.value`,
          [datasetId, columns[0].id, columns[1].id],
        );
        expect(rows.map(({ input, output }) => ({ input, output }))).toEqual(
          [...seeded].sort((a, b) => a.input.localeCompare(b.input)),
        );
        datasets.push({ id: datasetId, name, columns, rows });
        await testInfo.attach(`source-${side}-identities`, {
          contentType: "application/json",
          body: JSON.stringify(datasets.at(-1)),
        });
      });

      await test.step(
        `read the imported ${side} dataset`,
        async () => {
          await expect(page).toHaveURL(
            new RegExp(`/dashboard/develop/${datasetId}`),
            { timeout: UI_READY },
          );
          const outputId = datasets.at(-1)!.columns[1].id;
          await expect(
            page
              .locator(`.ag-row [col-id="${outputId}"]`)
              .filter({ hasText: seeded[0].output }),
          ).toHaveText(seeded[0].output, { timeout: UI_READY });
        },
        { timeout: UI_READY },
      );
    }

    const [left, right] = datasets;
    const inputId = left.columns[0].id;
    const leftId = left.columns[1].id;
    const rightId = right.columns[1].id;
    const responses: Response[] = [];
    page.on("response", (response) => {
      if (
        new URL(response.url()).pathname === comparePath(left.id) &&
        response.request().method() === "POST"
      )
        responses.push(response);
    });

    await test.step(
      "select the second dataset and compare by input",
      async () => {
        // DevelopDetailContext and the comparison picker expose these native labels.
        await page.goto(`/dashboard/develop/${left.id}`, {
          waitUntil: "domcontentloaded",
        });
        await page
          .getByRole("button", {
            name: "Compare with another dataset",
            exact: true,
          })
          .click();
        await page.getByText(right.name, { exact: true }).click();
        await page.getByRole("button", { name: "Open", exact: true }).click();
        await page.locator('input[type="radio"][value="input"]').check();
        await page
          .getByRole("button", { name: "Compare", exact: true })
          .last()
          .click();
        await page.getByRole("tab", { name: "Data", exact: true }).click();
      },
      { timeout: UI_READY },
    );

    const pages: ComparePage[] = [];
    const grid = page.locator(".experiment-view-grid"); // CompareData.jsx.
    for (const index of [0, 1]) {
      await test.step(
        `backend check 2: comparison block ${index} and its visible joined pairs`,
        async () => {
          if (index === 1) {
            await grid.locator(".ag-body-viewport").evaluate((element) => {
              element.scrollTop = element.scrollHeight;
            });
          }
          await expect
            .poll(
              () =>
                responses.some(
                  (response) =>
                    response.request().postDataJSON().current_page_index ===
                    index,
                ),
              { timeout: UI_READY },
            )
            .toBe(true);
          const response = responses.find(
            (item) =>
              item.request().postDataJSON().current_page_index === index,
          )!;
          expect(response.status()).toBe(200);
          const body: ComparePage = await response.json();
          pages.push(body);
          await testInfo.attach(`comparison-block-${index}`, {
            contentType: "application/json",
            body: JSON.stringify({
              request: response.request().postDataJSON(),
              body,
            }),
          });
          expect(body.status).toBe(true);
          expect(body.result.metadata).toEqual({
            compare_id: expect.any(String),
            total_rows: SHARED,
            total_pages: 2,
          });
          expect(body.result.table).toHaveLength(
            index === 0 ? PAGE_SIZE : SHARED - PAGE_SIZE,
          );
          expect(
            body.result.column_config
              .map(({ id, name }) => ({ id, name }))
              .sort((a, b) => a.id.localeCompare(b.id)),
          ).toEqual(
            [
              { id: inputId, name: "input" },
              { id: leftId, name: left.name },
              { id: rightId, name: right.name },
            ].sort((a, b) => a.id.localeCompare(b.id)),
          );
          expect(response.request().postDataJSON()).toEqual({
            dataset_ids: [right.id],
            base_column_name: "input",
            page_size: PAGE_SIZE,
            current_page_index: index,
            compare_id:
              index === 0 ? null : pages[0].result.metadata.compare_id,
          });
          expect(body.result.metadata.compare_id).toBe(
            pages[0].result.metadata.compare_id,
          );

          // Check every pair against the planted values, not against API text alone.
          // Scrolling is native; row-index/col-id are AG Grid's existing cell hooks.
          for (const [offset, row] of body.result.table.entries()) {
            const planted = expected.find(
              (value) => value.input === cellValue(row, inputId),
            );
            expect(planted).toBeDefined();
            expect([cellValue(row, leftId), cellValue(row, rightId)]).toEqual([
              planted!.left,
              planted!.right,
            ]);
            await grid.locator(".ag-body-viewport").evaluate(
              (element, top) => {
                element.scrollTop = top;
              },
              (index * PAGE_SIZE + offset) * ROW_HEIGHT,
            );
            const uiRow = grid.locator(
              `.ag-center-cols-container .ag-row[row-index="${index * PAGE_SIZE + offset}"]`,
            );
            await expect(uiRow.locator(`[col-id="${inputId}"]`)).toHaveText(
              planted!.input,
              { timeout: UI_READY },
            );
            await expect(uiRow.locator(`[col-id="${leftId}"]`)).toHaveText(
              planted!.left,
              { timeout: UI_READY },
            );
            await expect(uiRow.locator(`[col-id="${rightId}"]`)).toHaveText(
              planted!.right,
              { timeout: UI_READY },
            );
          }
          await testInfo.attach(`visible-block-${index}`, {
            contentType: "image/png",
            body: await page.screenshot(),
          });
        },
        { timeout: UI_READY },
      );
    }

    const ordered = pages.flatMap((body) => body.result.table); // grid row order
    const joined = ordered.map((row) => ({
      input: cellValue(row, inputId),
      left: cellValue(row, leftId),
      right: cellValue(row, rightId),
    }));
    expect([...joined].sort((a, b) => a.input.localeCompare(b.input))).toEqual(
      expected,
    );
    await expect(page.getByText("No Common rows", { exact: true })).toHaveCount(
      0,
      { timeout: UI_READY },
    );
    // Replay the same authenticated UI endpoint, preserving its comparison ID.
    const replay = await actor.api.post<ComparePage>(comparePath(left.id), {
      dataset_ids: [right.id],
      base_column_name: "input",
      page_size: PAGE_SIZE,
      current_page_index: 1,
      compare_id: pages[0].result.metadata.compare_id,
    });
    expect(replay.result).toEqual(pages[1].result);

    // Row detail: CompareDatasetDetailDrawer requests each row by its row_id and
    // prefetches its neighbours, so register the waits before the click.
    const compareId = pages[0].result.metadata.compare_id;
    const rowId = (index: number) => ordered[index].row_id as string;
    const rowLoaded = (index: number) =>
      page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
            rowPath(compareId, rowId(index)) &&
          response.request().method() === "GET",
        { timeout: UI_READY },
      );
    const drawer = page.locator(".MuiDrawer-paper").filter({
      has: page.getByText("Datapoint-compare", { exact: true }),
    });
    const expectRowDetail = async (index: number, response: Response) => {
      expect(response.status()).toBe(200);
      const body: RowDetail = await response.json();
      await testInfo.attach(`row-detail-${index}`, {
        contentType: "application/json",
        body: JSON.stringify(body),
      });
      expect(body.status).toBe(true);
      expect(body.result.table).toEqual([ordered[index]]);
      expect(body.result.prev_row_id).toBe(
        index === 0 ? null : rowId(index - 1),
      );
      expect(body.result.next_row_id).toBe(
        index === ordered.length - 1 ? null : rowId(index + 1),
      );
      // CompareDatasetDetailContent: one column per compared dataset, then each
      // dataset's output in the "Dataset Details" row.
      await expect(drawer.locator("thead th")).toHaveText([
        `A ${left.name}`,
        `B ${right.name}`,
      ]);
      const details = drawer
        .locator("table > tbody > tr")
        .nth(1)
        .locator(":scope > td");
      await expect(
        details.nth(0).getByText(joined[index].left, { exact: true }),
      ).toBeVisible();
      await expect(
        details.nth(1).getByText(joined[index].right, { exact: true }),
      ).toBeVisible();
      for (const other of [index - 1, index + 1].filter(
        (value) => value >= 0 && value < ordered.length,
      )) {
        await expect(details.nth(0)).not.toContainText(joined[other].left);
      }
    };
    const clickRow = async (index: number) => {
      await grid.locator(".ag-body-viewport").evaluate((element, top) => {
        element.scrollTop = top;
      }, index * ROW_HEIGHT);
      await grid
        .locator(
          `.ag-center-cols-container .ag-row[row-index="${index}"] [col-id="${leftId}"]`,
        )
        .click();
    };
    const last = ordered.length - 1;

    await test.step(
      "backend check 3: open the last joined row in row detail",
      async () => {
        const lastLoaded = rowLoaded(last);
        const previousLoaded = rowLoaded(last - 1);
        await clickRow(last);
        await expectRowDetail(last, await lastLoaded);
        await expect(
          drawer.getByRole("button", { name: "Next", exact: true }),
        ).toBeDisabled();
        await expect(
          drawer.getByRole("button", { name: "Prev", exact: true }),
        ).toBeEnabled();
        // Prev reads the prefetched row from block 0, across the block boundary.
        await drawer.getByRole("button", { name: "Prev", exact: true }).click();
        await expectRowDetail(last - 1, await previousLoaded);
        await expect(
          drawer.getByRole("button", { name: "Next", exact: true }),
        ).toBeEnabled();
        await testInfo.attach("row-detail-boundary", {
          contentType: "image/png",
          body: await page.screenshot(),
        });
      },
      { timeout: UI_READY },
    );

    await test.step(
      "backend check 3: the first joined row has no previous row",
      async () => {
        await page.keyboard.press("Escape");
        await expect(drawer).toHaveCount(0);
        const firstLoaded = rowLoaded(0);
        await clickRow(0);
        await expectRowDetail(0, await firstLoaded);
        await expect(
          drawer.getByRole("button", { name: "Prev", exact: true }),
        ).toBeDisabled();
        await expect(
          drawer.getByRole("button", { name: "Next", exact: true }),
        ).toBeEnabled();
      },
      { timeout: UI_READY },
    );

    await test.step(
      "backend check 4: the second output shows its word diff against the first",
      async () => {
        // CompareDatasetsView diffs each compared cell against the base dataset's
        // cell; body.result.table equalled ordered[0] in the previous step.
        expect((ordered[0][leftId] as CompareCell).cell_diff_value).toBeNull();
        expect((ordered[0][rightId] as CompareCell).cell_diff_value).toEqual([
          { text: joined[0].left, status: "removed" },
          { text: joined[0].right, status: "added" },
        ]);
        const details = drawer
          .locator("table > tbody > tr")
          .nth(1)
          .locator(":scope > td");
        await expect(
          details.nth(0).getByRole("tab", { name: "Difference", exact: true }),
        ).toHaveCount(0);
        const difference = details
          .nth(1)
          .getByRole("tab", { name: "Difference", exact: true });
        await difference.click();
        await expect(difference).toHaveAttribute("aria-selected", "true");
        // GenerateDiffText: one span per word, coloured by its get_diff status.
        const removed = details
          .nth(1)
          .getByText(joined[0].left, { exact: true });
        const added = details
          .nth(1)
          .getByText(joined[0].right, { exact: true });
        await expect(removed).toBeVisible();
        await expect(added).toBeVisible();
        const background = (word: typeof removed) =>
          word.evaluate((element) => getComputedStyle(element).backgroundColor);
        const [removedBackground, addedBackground] = [
          await background(removed),
          await background(added),
        ];
        expect([removedBackground, addedBackground]).not.toContain(
          "rgba(0, 0, 0, 0)",
        );
        expect(removedBackground).not.toBe(addedBackground);
        await testInfo.attach("row-detail-difference", {
          contentType: "image/png",
          body: await page.screenshot(),
        });
      },
      { timeout: UI_READY },
    );
    // actor, browser context and StateProbe are disposed by lib/fixtures.ts.
  },
);

// Lifecycle regressions use the same authenticated actor, native CSV importer,
// PG probe and real endpoints as DATA-E2E-005. Routes delay/reuse real responses
// or change a request to a real missing resource; no response body is invented.
type LifecycleFixtures = { page: Page; actor: TestActor; probe: StateProbe };
// urls.py / GetCompareDatasetRow.delete: metadata invalidation precedes cleanup.
const deletePath = (id: string) => `/model-hub/datasets/delete-compare/${id}/`;
// DatasetUpdateCellValueRequestSerializer and DATA-E2E-002's captured edit body.
const updateCellPath = (id: string) =>
  `/model-hub/develops/${id}/update_cell_value/`;
const downloadPath = (id: string) => `${comparePath(id)}download/`;
const expiredMessage =
  "This comparison expired and is being rebuilt. Download it again once the table has loaded.";

async function openLifecycleComparison(
  { page, actor, probe }: LifecycleFixtures,
  info: TestInfo,
  initiallyEmpty = false,
) {
  test.setTimeout(600_000); // Two imports, comparison build and delayed block/detail checks.
  page.setDefaultTimeout(UI_READY);
  // Keep the initial viewport (including AG Grid's row buffer) within blocks
  // 0/1; blocks 2/3 below must not already be cached when invalidation starts.
  await page.setViewportSize({ width: 1280, height: 720 });
  const suffix = randomUUID();
  const datasets: Dataset[] = [];
  for (const side of ["left", "right"] as const) {
    const name = `e2e-compare-${side}-${suffix}`;
    const seeded = Array.from({ length: 32 }, (_, index) => ({
      input: `${initiallyEmpty && side === "right" ? "disjoint" : "join"}-${suffix}-${index}`,
      output: `${side}-${suffix}-${index}`,
    }));
    await page.goto("/dashboard/develop", { waitUntil: "domcontentloaded" });
    await page
      .getByRole("button", { name: /^add dataset$/i })
      .first()
      .click();
    await page.getByText("Upload a file (JSON, CSV)", { exact: true }).click();
    const dialog = page
      .getByRole("dialog")
      .filter({ has: page.getByText("Upload a File", { exact: true }) });
    await dialog.locator('input[type="file"]').setInputFiles({
      name: `compare-${side}.csv`,
      mimeType: "text/csv",
      buffer: Buffer.from(
        [
          "input,output",
          ...seeded.map((row) => `${row.input},${row.output}`),
          "",
        ].join("\n"),
      ),
    });
    await dialog.getByPlaceholder("Dataset Name").fill(name);
    const uploaded = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === UPLOAD &&
        response.request().method() === "POST",
    );
    await dialog.getByRole("button", { name: "Upload", exact: true }).click();
    const response = await uploaded;
    expect(response.status()).toBe(200);
    const id: string = (await response.json()).result.dataset_id;
    await expect
      .poll(
        async () =>
          (
            await probe.pg<{ n: string }>(
              "SELECT count(*) AS n FROM model_hub_cell WHERE dataset_id = $1 AND NOT deleted",
              [id],
            )
          )[0].n,
        POLL.ASYNC_JOB,
      )
      .toBe("64");
    expect(
      await probe.pg(
        "SELECT organization_id, workspace_id FROM model_hub_dataset WHERE id = $1",
        [id],
      ),
    ).toEqual([
      {
        organization_id: actor.organizationId,
        workspace_id: actor.workspaceId,
      },
    ]);
    const columns = await probe.pg<Column>(
      "SELECT id, name FROM model_hub_column WHERE dataset_id = $1 AND NOT deleted ORDER BY name",
      [id],
    );
    expect(columns.map((column) => column.name)).toEqual(["input", "output"]);
    const rows = await probe.pg<SourceRow>(
      `SELECT i.row_id, i.value AS input, o.value AS output FROM model_hub_cell i
       JOIN model_hub_cell o ON o.dataset_id = i.dataset_id AND o.row_id = i.row_id
       WHERE i.dataset_id = $1 AND i.column_id = $2 AND o.column_id = $3
       AND NOT i.deleted AND NOT o.deleted ORDER BY i.value`,
      [id, columns[0].id, columns[1].id],
    );
    expect(rows.map(({ input, output }) => ({ input, output }))).toEqual(
      [...seeded].sort((a, b) => a.input.localeCompare(b.input)),
    );
    datasets.push({ id, name, columns, rows });
    await expect(page).toHaveURL(new RegExp(`/dashboard/develop/${id}`));
    await expect(
      page
        .locator(`.ag-row [col-id="${columns[1].id}"]`)
        .filter({ hasText: seeded[0].output }),
    ).toHaveText(seeded[0].output);
  }
  const [left, right] = datasets;
  const requests: Request[] = [];
  const responses: Response[] = [];
  page.on("request", (request) => {
    if (
      new URL(request.url()).pathname === comparePath(left.id) &&
      request.method() === "POST"
    )
      requests.push(request);
  });
  page.on("response", (response) => {
    if (
      new URL(response.url()).pathname === comparePath(left.id) &&
      response.request().method() === "POST"
    )
      responses.push(response);
  });
  await page.goto(`/dashboard/develop/${left.id}`, {
    waitUntil: "domcontentloaded",
  });
  await page
    .getByRole("button", { name: "Compare with another dataset", exact: true })
    .click();
  await page.getByText(right.name, { exact: true }).click();
  await page.getByRole("button", { name: "Open", exact: true }).click();
  await page.locator('input[type="radio"][value="input"]').check();
  await page
    .getByRole("button", { name: "Compare", exact: true })
    .last()
    .click();
  const loaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === comparePath(left.id) &&
      response.request().method() === "POST",
  );
  await page.getByRole("tab", { name: "Data", exact: true }).click();
  const response = await loaded;
  expect(response.status()).toBe(200);
  const first: ComparePage = await response.json();
  expect(first.result.metadata).toEqual({
    compare_id: expect.any(String),
    total_rows: initiallyEmpty ? 0 : 32,
    total_pages: initiallyEmpty ? 0 : 4,
  });
  const grid = page.locator(".experiment-view-grid");
  const drawer = page
    .locator(".MuiDrawer-paper")
    .filter({ has: page.getByText("Datapoint-compare", { exact: true }) });
  if (initiallyEmpty)
    await expect(
      page.getByText("No Common rows", { exact: true }),
    ).toBeVisible();
  else {
    const firstInput = cellValue(first.result.table[0], left.columns[0].id);
    const planted = left.rows.find((row) => row.input === firstInput)!;
    await expect(
      grid.locator(
        `.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`,
      ),
    ).toHaveText(planted.output);
  }
  await info.attach("lifecycle-source-and-first-block", {
    contentType: "application/json",
    body: JSON.stringify({ datasets, first }),
  });
  return { left, right, grid, drawer, first, requests, responses };
}

async function scrollComparison(page: Page, index: number) {
  await page
    .locator(".experiment-view-grid .ag-body-viewport")
    .evaluate((element, top) => {
      element.scrollTop = top;
    }, index * ROW_HEIGHT);
}

async function openEarlierDrawer(
  page: Page,
  state: Awaited<ReturnType<typeof openLifecycleComparison>>,
) {
  const { first, left, drawer, grid } = state;
  const id = first.result.metadata.compare_id;
  const detail = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
      rowPath(id, first.result.table[0].row_id as string),
  );
  const neighbour = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
      rowPath(id, first.result.table[1].row_id as string),
  );
  await grid
    .locator(
      `.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`,
    )
    .click();
  expect((await detail).status()).toBe(200);
  expect((await neighbour).status()).toBe(200); // Finish actual detail prefetch before invalidating the ID.
  await expect(drawer).toBeVisible();
  await expect(
    drawer.getByText(cellValue(first.result.table[0], left.columns[1].id), {
      exact: true,
    }),
  ).toBeVisible();
}

test(
  "DATA-E2E-006: Compare lifecycle: later-block fresh ID retires the earlier drawer and delayed competing generation",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "DATA-E2E-006",
      area: "datasets",
      userGoal:
        "Keep comparison rows and detail on the adopted snapshot when concurrent later blocks rebuild an expired comparison",
      steps: [
        "upload two matching datasets, compare by input and open the first row detail",
        "retain real earlier-page reads before deleting the comparison through its API, then hold both old-ID page two and page three rebuild responses while scrolling the real grid",
        "release page two first, confirm the old drawer closes, then release the competing page three response",
        "revisit rows thirty and thirty-one and open row thirty detail from the adopted snapshot",
        "return to row zero and open its rebuilt detail",
      ],
      backendChecks: [
        "synthetic uploaded rows and dataset scope match the authenticated actor in Postgres",
        "the next browser comparison POST after adoption uses the winning compare_id and orphan cleanup returns 200",
        "page three replay and rendered row IDs equal the adopted snapshot and exclude every delayed orphan row ID",
        "row thirty and row zero detail requests use the adopted compare_id and return the adopted rows",
      ],
    }),
  },
  async ({ page, actor, probe }, info) => {
    const state = await openLifecycleComparison({ page, actor, probe }, info);
    const { left, first, grid, drawer, responses } = state;
    const oldId = first.result.metadata.compare_id;
    await openEarlierDrawer(page, state);
    const held = new Map<
      number,
      { route: Route; response: APIResponse; body: ComparePage }
    >();
    const pendingRoutes = new Set<Route>();
    const earlierReads: { request: unknown; body: ComparePage }[] = [];
    let prepared: Promise<Map<number, APIResponse>> | undefined;
    let winningId: string | undefined;
    let cleaningUp = false;
    await page.route(`**${comparePath(left.id)}`, async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      if (cleaningUp) {
        await route.abort();
        return;
      }
      const sent = route.request().postDataJSON();
      if (winningId && sent.compare_id === winningId) {
        await route.continue();
        return;
      }
      pendingRoutes.add(route);
      expect(sent).toEqual({
        dataset_ids: [state.right.id],
        base_column_name: "input",
        page_size: PAGE_SIZE,
        current_page_index: expect.any(Number),
        compare_id: oldId,
      });
      expect([0, 1, 2, 3]).toContain(sent.current_page_index);
      // AG Grid can request page one alongside page two. Holding both would
      // consume its two load slots, preventing page three; allowing page one to
      // rebuild would instead steal the generation. Retain real reads of pages
      // zero/one BEFORE deletion, then deliver those exact responses unchanged.
      // This models earlier-page reads already in flight at expiry, not fixture
      // payloads. Only pages two/three hit the missing ID and mint generations.
      prepared ??= (async () => {
        const cached = new Map<number, APIResponse>();
        for (const index of [0, 1]) {
          if (cleaningUp) break;
          const request = { ...sent, current_page_index: index };
          const response = await route.fetch({
            postData: request,
            timeout: UI_READY,
          });
          const body: ComparePage = await response.json();
          expect(response.status()).toBe(200);
          expect(body.result.metadata).toEqual({
            compare_id: oldId,
            total_rows: 32,
            total_pages: 4,
          });
          expect(body.result.table).toHaveLength(PAGE_SIZE);
          cached.set(index, response);
          earlierReads.push({ request, body });
        }
        // GetCompareDatasetRow.delete invalidates metadata synchronously. No
        // bucket manipulation, clock control or write through StateProbe.
        if (!cleaningUp) await actor.api.delete(deletePath(oldId));
        return cached;
      })();
      const cached = await prepared;
      if (cleaningUp) return;
      if (sent.current_page_index < 2) {
        pendingRoutes.delete(route);
        await route.fulfill({ response: cached.get(sent.current_page_index)! });
        return;
      }
      const response = await route.fetch({ timeout: UI_READY });
      if (cleaningUp) return;
      expect(response.status()).toBe(200);
      const body: ComparePage = await response.json();
      expect(body.result.table).toHaveLength(
        sent.current_page_index === 2 ? PAGE_SIZE : 2,
      );
      expect(body.result.metadata.compare_id).not.toBe(oldId);
      expect(held.has(sent.current_page_index)).toBe(false);
      held.set(sent.current_page_index, { route, response, body });
    });
    try {
      // Both actual old-ID browser requests must reach the backend and finish
      // rebuilding before either response is delivered to the datasource.
      await scrollComparison(page, 20);
      await expect.poll(() => held.has(2), { timeout: UI_READY }).toBe(true);
      await scrollComparison(page, 30);
      await expect.poll(() => held.has(3), { timeout: UI_READY }).toBe(true);
      const winner = held.get(2)!;
      const loser = held.get(3)!;
      const newId = winner.body.result.metadata.compare_id;
      const orphanId = loser.body.result.metadata.compare_id;
      expect(orphanId).not.toBe(newId);
      for (const [index, entry] of held) {
        expect(entry.route.request().postDataJSON()).toEqual({
          dataset_ids: [state.right.id],
          base_column_name: "input",
          page_size: PAGE_SIZE,
          current_page_index: index,
          compare_id: oldId,
        });
      }
      const nextRequest = page.waitForRequest(
        (request) =>
          new URL(request.url()).pathname === comparePath(left.id) &&
          request.method() === "POST",
        { timeout: UI_READY },
      );
      winningId = newId;
      pendingRoutes.delete(winner.route);
      await winner.route.fulfill({ response: winner.response });
      expect((await nextRequest).postDataJSON().compare_id).toBe(newId);
      await expect(drawer).toHaveCount(0);
      await scrollComparison(page, 0);
      await expect
        .poll(
          () =>
            responses.some((response) => {
              const sent = response.request().postDataJSON();
              return sent.compare_id === newId && sent.current_page_index === 0;
            }),
          { timeout: UI_READY },
        )
        .toBe(true);
      const reloaded = responses.find((response) => {
        const sent = response.request().postDataJSON();
        return sent.compare_id === newId && sent.current_page_index === 0;
      })!;
      expect(reloaded.status()).toBe(200);
      const rebuilt: ComparePage = await reloaded.json();
      expect(rebuilt.result.metadata.compare_id).toBe(newId);
      const orphanDeleted = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === deletePath(orphanId) &&
          response.request().method() === "DELETE",
      );
      pendingRoutes.delete(loser.route);
      await loser.route.fulfill({ response: loser.response });
      expect((await orphanDeleted).status()).toBe(200);
      // Revisit the competing block, not only row zero: its visible row IDs
      // must come from the adopted server snapshot, never the delayed orphan.
      const adoptedBlock = await actor.api.post<ComparePage>(
        comparePath(left.id),
        {
          dataset_ids: [state.right.id],
          base_column_name: "input",
          page_size: PAGE_SIZE,
          current_page_index: 3,
          compare_id: newId,
        },
      );
      expect(adoptedBlock.result.metadata.compare_id).toBe(newId);
      expect(adoptedBlock.result.table).toHaveLength(2);
      const orphanRowIds = loser.body.result.table.map((row) => row.row_id);
      const revisitGeometry: unknown[] = [];
      for (const [offset, row] of adoptedBlock.result.table.entries()) {
        expect(orphanRowIds).not.toContain(row.row_id);
        const rendered = grid.locator(
          `.ag-center-cols-container .ag-row[row-index="${30 + offset}"]`,
        );
        // R3's one-shot 3600px scroll clamped to 1513px: unloaded SSRM stubs
        // were 36px, while loaded rows become 120px. As pages 1/2 loaded, row
        // 30 moved out of the viewport/buffer before a browser page-3 load.
        // Keep navigating natively as that geometry changes, not retrying a
        // datasource or supplying rows. An exact ID list still rejects clones.
        await expect
          .poll(
            async () => {
              await scrollComparison(page, 30 + offset);
              const geometry = await grid
                .locator(".ag-body-viewport")
                .evaluate((element) => ({
                  top: element.scrollTop,
                  height: element.scrollHeight,
                  viewport: element.clientHeight,
                }));
              const rowIds = await rendered.evaluateAll((elements) =>
                elements.map((element) => element.getAttribute("row-id")),
              );
              revisitGeometry.push({ index: 30 + offset, ...geometry, rowIds });
              return rowIds;
            },
            { timeout: UI_READY },
          )
          .toEqual([row.row_id]);
        await expect(rendered).toHaveAttribute("row-id", row.row_id as string, {
          timeout: UI_READY,
        });
        const input = cellValue(row, left.columns[0].id);
        const plantedLeft = left.rows.find((source) => source.input === input)!;
        const plantedRight = state.right.rows.find(
          (source) => source.input === input,
        )!;
        expect([
          cellValue(row, left.columns[1].id),
          cellValue(row, state.right.columns[1].id),
        ]).toEqual([plantedLeft.output, plantedRight.output]);
        await expect(
          rendered.locator(`[col-id="${left.columns[0].id}"]`),
        ).toHaveText(input);
        await expect(
          rendered.locator(`[col-id="${left.columns[1].id}"]`),
        ).toHaveText(plantedLeft.output);
        await expect(
          rendered.locator(`[col-id="${state.right.columns[1].id}"]`),
        ).toHaveText(plantedRight.output);
      }
      // The API replay alone is not evidence that AG Grid loaded page three.
      const browserBlockResponse = responses.find((response) => {
        const sent = response.request().postDataJSON();
        return sent.compare_id === newId && sent.current_page_index === 3;
      });
      expect(browserBlockResponse).toBeDefined();
      expect(browserBlockResponse!.status()).toBe(200);
      const browserBlock: ComparePage = await browserBlockResponse!.json();
      expect(browserBlock).toEqual(adoptedBlock);
      await info.attach("adopted-page-three-viewport", {
        contentType: "application/json",
        body: JSON.stringify({
          revisitGeometry,
          request: browserBlockResponse!.request().postDataJSON(),
          browserBlock,
          orphanRowIds,
        }),
      });
      // This is the LOSING block's row, not the winner's row twenty or only row
      // zero. Its click must resolve under the adopted ID after the late reply.
      const adoptedRow = adoptedBlock.result.table[0];
      const adoptedDetail = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
            rowPath(newId, adoptedRow.row_id as string) &&
          response.request().method() === "GET",
      );
      await scrollComparison(page, 30);
      await grid
        .locator(
          `.ag-center-cols-container .ag-row[row-index="30"] [col-id="${left.columns[1].id}"]`,
        )
        .click();
      const detailResponse = await adoptedDetail;
      expect(detailResponse.status()).toBe(200);
      const detailBody: RowDetail = await detailResponse.json();
      expect(detailBody.result.table).toEqual([adoptedRow]);
      await expect(
        drawer.getByText(cellValue(adoptedRow, left.columns[1].id), {
          exact: true,
        }),
      ).toBeVisible();
      await expect(
        drawer.getByText(cellValue(adoptedRow, state.right.columns[1].id), {
          exact: true,
        }),
      ).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(drawer).toHaveCount(0);
      await scrollComparison(page, 0);
      const row = rebuilt.result.table[0];
      await expect(
        grid.locator(`.ag-center-cols-container .ag-row[row-index="0"]`),
      ).toHaveAttribute("row-id", row.row_id as string);
      const detail = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname ===
          rowPath(newId, row.row_id as string),
      );
      await grid
        .locator(
          `.ag-center-cols-container .ag-row[row-index="0"] [col-id="${left.columns[1].id}"]`,
        )
        .click();
      const firstDetail = await detail;
      expect(firstDetail.status()).toBe(200);
      expect((await firstDetail.json()).result.table).toEqual([row]);
      await expect(
        drawer.getByText(cellValue(row, left.columns[1].id), { exact: true }),
      ).toBeVisible();
      await info.attach("real-rollover-and-competing-blocks", {
        contentType: "application/json",
        body: JSON.stringify({
          oldId,
          earlierReads,
          winnerRequest: winner.route.request().postDataJSON(),
          loserRequest: loser.route.request().postDataJSON(),
          winner: winner.body,
          loser: loser.body,
          rebuilt,
          adoptedBlock,
          detailBody,
        }),
      });
    } finally {
      // Track routes before fetch, not just successful replies: an assertion or
      // fetch failure must not strand an SSRM slot or make unrouteAll wait forever.
      cleaningUp = true;
      try {
        await Promise.all([...pendingRoutes].map((route) => route.abort()));
      } finally {
        await page.unrouteAll({ behavior: "wait" });
      }
    }
  },
);

async function assertSameIdEmptyRefresh(
  page: Page,
  state: Awaited<ReturnType<typeof openLifecycleComparison>>,
  compareId: string,
  purge: boolean,
  info: TestInfo,
) {
  const bridge = await observeCompareGrid(page);
  const { grid, requests } = state;
  const postsBefore = requests.length;
  try {
    const emptyStore = [{ route: [], rowCount: 0, lastRowIndexKnown: true }];
    await expect
      .poll(() => bridge.evaluate((value) => value.snapshot()), {
        timeout: UI_READY,
      })
      .toEqual({
        compareId,
        displayedRows: 0,
        stores: emptyStore,
        datasourceUnchanged: true,
        requests: [],
        successes: [],
        refreshes: [],
        failures: 0,
      });
    const pending = await bridge.evaluate(
      (value, shouldPurge) => value.refresh(shouldPurge),
      purge,
    );
    expect(pending.compareId).toBe(compareId);
    expect(pending.refreshes).toEqual([{ purge, compareId }]);
    // Both AG Grid 35 paths replace a known-empty cache with an unknown-size
    // one-row placeholder. Sorting a zero-row store does NOT do this.
    expect(pending.stores).toEqual([
      { route: [], rowCount: 1, lastRowIndexKnown: false },
    ]);
    expect(pending.displayedRows).toBe(1);
    const settled = {
      compareId,
      displayedRows: 0,
      stores: emptyStore,
      datasourceUnchanged: true,
      requests: [{ startRow: 0, endRow: PAGE_SIZE, compareId }],
      successes: [{ rows: 0, rowCount: 0, compareId }],
      refreshes: [{ purge, compareId }],
      failures: 0,
    };
    // Observe real LazyStore -> real datasource -> real success([], 0). Neither
    // manually calling getRows nor a no-POST assertion alone proves this path.
    await expect
      .poll(() => bridge.evaluate((value) => value.snapshot()), {
        timeout: UI_READY,
      })
      .toEqual(settled);
    await expect(
      page.getByText("No Common rows", { exact: true }),
    ).toBeVisible();
    await expect(grid.locator(".ag-center-cols-container .ag-row")).toHaveCount(
      0,
    );
    await expect(grid.locator(".ag-floating-bottom .ag-row")).toHaveCount(0);
    await page.waitForTimeout(1_000); // Bounded observation for extra loads, purges or POSTs.
    expect(await bridge.evaluate((value) => value.snapshot())).toEqual(settled);
    expect(requests).toHaveLength(postsBefore);
    await info.attach(
      `same-id-empty-refresh-${purge ? "purge" : "non-purge"}`,
      {
        contentType: "application/json",
        body: JSON.stringify({
          pending,
          settled,
          postsBefore,
          postsAfter: requests.length,
        }),
      },
    );
  } finally {
    await bridge.evaluate((value) => value.restore());
    await bridge.dispose();
  }
}

for (const initiallyEmpty of [false, true]) {
  const id = initiallyEmpty ? "DATA-E2E-008" : "DATA-E2E-007";
  test(
    `${id}: Compare lifecycle: ${initiallyEmpty ? "initial empty refresh" : "nonempty to zero rollover"} is terminal in real AG Grid`,
    {
      tag: ["@flow"],
      annotation: flowAnnotation({
        id,
        area: "datasets",
        userGoal: initiallyEmpty
          ? "Keep an initially empty comparison settled during same-ID grid refresh and explicit download recovery"
          : "Retire old comparison rows and detail when a later block rebuilds to zero, then keep same-ID refreshes settled",
        steps: [
          initiallyEmpty
            ? "upload disjoint datasets and compare by input"
            : "upload matching datasets, compare by input, open detail, update only the synthetic peer inputs through the public cell-edit API to remove all overlap, then invalidate metadata through the comparison DELETE API",
          "observe a real zero-row comparison with no old detail or summary rows",
          "call the existing real grid API for non-purge and purge refresh without clearing the comparison ID",
          initiallyEmpty
            ? "download the uncached empty comparison and observe one explicit fresh comparison build"
            : "sort the empty result and observe that it remains empty without another POST",
        ],
        backendChecks: [
          ...(initiallyEmpty
            ? []
            : [
                "public cell edits preserve the synthetic peer row IDs and outputs and leave zero shared input values in Postgres",
              ]),
          "the real comparison endpoint returns a server-minted ID and zero row/page counts",
          "each same-ID refresh completes locally with no additional comparison POSTs during a bounded observation window",
          initiallyEmpty
            ? "download returns the typed compare_not_found response and recovery builds once with a null request ID"
            : "the later-block rollover makes at most two comparison POSTs and adopts a new ID",
        ],
        browserChecks: [
          "real AG Grid store state transitions from known zero to unknown one, then back to known zero in both refresh modes",
          "each refresh invokes the real datasource once and succeeds with zero rows, without another purge or ID change",
          "no common rows, no rendered data or summary rows, and no stale detail drawer remain",
        ],
      }),
    },
    async ({ page, actor, probe }, info) => {
      const state = await openLifecycleComparison(
        { page, actor, probe },
        info,
        initiallyEmpty,
      );
      const { left, right, first, grid, drawer, requests } = state;
      let requestsBeforeRollover = requests.length;
      let terminalId = first.result.metadata.compare_id;
      if (!initiallyEmpty) {
        // Lifecycle budget plus UI_READY for cell edits and ASYNC_JOB for their
        // PG readback. Comparison reads PG directly, so no CDC budget applies.
        test.setTimeout(720_000);
        await openEarlierDrawer(page, state);
        const changed = right.rows.map((row) => ({
          ...row,
          input: `disjoint-${row.input}`,
        }));
        expect(changed).toHaveLength(32);
        await test.step(
          "make only the synthetic peer join values disjoint through the cell-edit API",
          async () => {
            for (const row of changed) {
              // Same public write as DATA-E2E-002, pinned from
              // DatasetUpdateCellValueRequestSerializer; not a SQL mutation.
              const updated = await actor.api.post<{
                status: boolean;
                result: string;
              }>(updateCellPath(right.id), {
                column_id: right.columns[0].id,
                row_id: row.row_id,
                new_value: row.input,
              });
              // UpdateCellValueView / DevelopDatasetMessageResponseSerializer.
              expect(updated).toEqual({
                status: true,
                result: "Cell value updated successfully",
              });
            }
          },
          { timeout: UI_READY },
        );
        await expect
          .poll(
            () =>
              probe.pg<SourceRow>(
                `SELECT i.row_id, i.value AS input, o.value AS output FROM model_hub_cell i
                 JOIN model_hub_cell o ON o.dataset_id = i.dataset_id AND o.row_id = i.row_id
                 WHERE i.dataset_id = $1 AND i.column_id = $2 AND o.column_id = $3
                 AND NOT i.deleted AND NOT o.deleted ORDER BY i.value`,
                [right.id, right.columns[0].id, right.columns[1].id],
              ),
            POLL.ASYNC_JOB,
          )
          .toEqual(changed);
        const overlap = await probe.pg<{ n: string }>(
          `SELECT count(*) AS n FROM model_hub_cell l
           JOIN model_hub_cell r ON r.value = l.value
           WHERE l.dataset_id = $1 AND l.column_id = $2
             AND r.dataset_id = $3 AND r.column_id = $4
             AND NOT l.deleted AND NOT r.deleted`,
          [left.id, left.columns[0].id, right.id, right.columns[0].id],
        );
        expect(overlap).toEqual([{ n: "0" }]);
        await info.attach("public-cell-edits-zero-overlap", {
          contentType: "application/json",
          body: JSON.stringify({
            datasetId: right.id,
            inputColumnId: right.columns[0].id,
            before: right.rows,
            after: changed,
            overlap,
          }),
        });
        // No narrow public expiry hook exists. The supported DELETE removes
        // metadata synchronously, exercising a real nonempty -> empty rebuild.
        await actor.api.delete(deletePath(first.result.metadata.compare_id));
        requestsBeforeRollover = requests.length;
        const rollover = await observeCompareGrid(page);
        const retirements: Response[] = [];
        const recordRetirement = (response: Response) => {
          if (
            response.request().method() === "DELETE" &&
            new URL(response.url()).pathname.startsWith(
              "/model-hub/datasets/delete-compare/",
            )
          )
            retirements.push(response);
        };
        page.on("response", recordRetirement);
        try {
          expect(
            (await rollover.evaluate((value) => value.snapshot())).compareId,
          ).toBe(terminalId);
          await scrollComparison(page, 30);
          // AG Grid can load page one AND page two concurrently. R3 adopted
          // page one's empty ID before page two replied; waiting only for >=2
          // mistook that stale reply for the winner. Read the actual ref, then
          // account for EVERY HTTP response and real datasource completion.
          await expect
            .poll(
              async () => {
                const snapshot = await rollover.evaluate((value) =>
                  value.snapshot(),
                );
                const sent = requests.slice(requestsBeforeRollover);
                const received = state.responses.filter((response) =>
                  sent.includes(response.request()),
                );
                return (
                  snapshot.compareId !== terminalId &&
                  snapshot.displayedRows === 0 &&
                  snapshot.successes.length === 1 &&
                  sent.length > 0 &&
                  received.length === sent.length &&
                  snapshot.failures === sent.length
                );
              },
              { timeout: UI_READY },
            )
            .toBe(true);
          const settled = await rollover.evaluate((value) => value.snapshot());
          expect(settled.compareId).toEqual(expect.any(String));
          expect(settled.compareId).not.toBe(terminalId);
          const oldId = terminalId;
          terminalId = settled.compareId!;
          const sent = requests.slice(requestsBeforeRollover);
          expect(sent.length).toBeGreaterThan(0);
          expect(sent.length).toBeLessThanOrEqual(2); // SSRM's two load slots.
          const generations = [];
          for (const request of sent) {
            const body = request.postDataJSON();
            expect(body).toEqual({
              dataset_ids: [right.id],
              base_column_name: "input",
              page_size: PAGE_SIZE,
              current_page_index: expect.any(Number),
              compare_id: oldId,
            });
            expect([1, 2, 3]).toContain(body.current_page_index);
            const response = state.responses.find(
              (item) => item.request() === request,
            )!;
            expect(response.status()).toBe(200);
            const empty: ComparePage = await response.json();
            expect(empty.status).toBe(true);
            expect(empty.result.table).toEqual([]);
            expect(empty.result.metadata).toEqual({
              compare_id: expect.any(String),
              total_rows: 0,
              total_pages: 0,
            });
            expect(empty.result.metadata.compare_id).not.toBe(oldId);
            generations.push({ request: body, body: empty });
          }
          const ids = generations.map(
            ({ body }) => body.result.metadata.compare_id,
          );
          expect(new Set(ids).size).toBe(sent.length);
          const adopted = generations.filter(
            ({ body }) => body.result.metadata.compare_id === terminalId,
          );
          expect(adopted).toHaveLength(1);
          // One adoption/purge, one local zero success, and one fail for each
          // retired old-store callback (winner included). No second adoption.
          expect(settled).toEqual({
            compareId: terminalId,
            displayedRows: 0,
            stores: [{ route: [], rowCount: 0, lastRowIndexKnown: true }],
            datasourceUnchanged: true,
            requests: [
              ...sent.map((request) => ({
                startRow: request.postDataJSON().current_page_index * PAGE_SIZE,
                endRow:
                  (request.postDataJSON().current_page_index + 1) * PAGE_SIZE,
                compareId: oldId,
              })),
              { startRow: 0, endRow: PAGE_SIZE, compareId: terminalId },
            ],
            successes: [{ rows: 0, rowCount: 0, compareId: terminalId }],
            refreshes: [{ purge: true, compareId: terminalId }],
            failures: sent.length,
          });
          for (const id of ids.filter((id) => id !== terminalId)) {
            await expect
              .poll(
                () =>
                  retirements
                    .filter(
                      (response) =>
                        new URL(response.url()).pathname === deletePath(id),
                    )
                    .map((response) => response.status()),
                { timeout: UI_READY },
              )
              .toEqual([404]); // Empty IDs have no stored metadata to delete.
          }
          await info.attach("real-zero-rollover", {
            contentType: "application/json",
            body: JSON.stringify(adopted[0].body),
          });
          await info.attach("zero-rollover-generations", {
            contentType: "application/json",
            body: JSON.stringify({
              oldId,
              terminalId,
              generations,
              responseOrder: state.responses
                .filter((response) => sent.includes(response.request()))
                .map(
                  (response) =>
                    response.request().postDataJSON().current_page_index,
                ),
              settled,
              retirements: retirements.map((response) => ({
                path: new URL(response.url()).pathname,
                status: response.status(),
              })),
            }),
          });
        } finally {
          page.off("response", recordRetirement);
          await rollover.evaluate((value) => value.restore());
          await rollover.dispose();
        }
      }
      await expect(drawer).toHaveCount(0);
      await expect(
        page.getByText("No Common rows", { exact: true }),
      ).toBeVisible();
      await expect(
        grid.locator(".ag-center-cols-container .ag-row"),
      ).toHaveCount(0);
      await expect(grid.locator(".ag-floating-bottom .ag-row")).toHaveCount(0);
      for (const purge of [false, true]) {
        await assertSameIdEmptyRefresh(page, state, terminalId, purge, info);
      }
      if (initiallyEmpty) {
        // No column_config is returned for an initially empty join, hence no
        // sortable header. Exercise the real caller's explicit purge/reset via
        // download instead: this uncached ID gets the actual typed 404.
        const missed = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === downloadPath(left.id),
        );
        const rebuilt = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === comparePath(left.id),
        );
        await page
          .getByRole("button", { name: "Download comparison CSV", exact: true })
          .click();
        const missing = await missed;
        expect(missing.status()).toBe(404);
        expect((await missing.json()).code).toBe("compare_not_found");
        const response = await rebuilt;
        expect(response.status()).toBe(200);
        expect(response.request().postDataJSON().compare_id).toBeNull();
        const empty: ComparePage = await response.json();
        expect(empty.result.metadata.total_rows).toBe(0);
        expect(empty.result.metadata.compare_id).not.toBe(
          first.result.metadata.compare_id,
        );
      } else {
        // Retain the sorting control, but do not count it as refresh proof:
        // refreshAfterSort preserves a known zero-row cache without a load.
        // The same-ID API refresh/datasource paths were asserted above.
        const header = grid.locator(".ag-header-cell").first();
        await header.focus();
        await header.press("Enter");
        await expect(header).toHaveAttribute("aria-sort", "ascending");
      }
      const count = requests.length;
      await expect(
        page.getByText("No Common rows", { exact: true }),
      ).toBeVisible();
      await expect(
        grid.locator(".ag-center-cols-container .ag-row"),
      ).toHaveCount(0);
      // Observe a bounded quiet window so an asynchronous purge/POST loop cannot
      // pass just because the first empty overlay was briefly visible.
      await page.waitForTimeout(1_000);
      expect(requests).toHaveLength(count);
      if (initiallyEmpty) expect(count).toBe(2);
      else {
        expect(count).toBeGreaterThan(requestsBeforeRollover);
        expect(count).toBeLessThanOrEqual(requestsBeforeRollover + 2);
      }
    },
  );
}

test(
  "DATA-E2E-009: Compare lifecycle: real typed download miss rebuilds once and only the next explicit download uses the new ID",
  {
    tag: ["@flow"],
    annotation: flowAnnotation({
      id: "DATA-E2E-009",
      area: "datasets",
      userGoal:
        "Recover a genuinely expired comparison download without rebuilding on an unrelated dataset error or automatically retrying CSV",
      steps: [
        "upload matching datasets and compare by input",
        "request a download against a real missing peer and observe no comparison rebuild",
        "expire the real comparison, download again and read the typed recovery message",
        "wait for the rebuilt table, then explicitly download again using the adopted ID",
      ],
      backendChecks: [
        "a real generic dataset 404 is distinct from the exact typed compare_not_found envelope",
        "typed download failure preserves the submitted old ID and starts a fresh comparison request with a null ID",
        "no automatic CSV retry occurs and the next explicit download uses the rebuilt ID and contains both planted datasets",
      ],
    }),
  },
  async ({ page, actor, probe }, info) => {
    const state = await openLifecycleComparison({ page, actor, probe }, info);
    const { left, right, first, grid, requests } = state;
    const oldId = first.result.metadata.compare_id;
    const downloads: Request[] = [];
    page.on("request", (request) => {
      if (
        new URL(request.url()).pathname === downloadPath(left.id) &&
        request.method() === "POST"
      )
        downloads.push(request);
    });
    const button = page.getByRole("button", {
      name: "Download comparison CSV",
      exact: true,
    });
    // Dataset/access 404 is a different real backend response, and must not run
    // the recovery callback. Substitute only the requested peer, never the body.
    await page.route(
      `**${downloadPath(left.id)}`,
      async (route) => {
        const response = await route.fetch({
          postData: {
            ...route.request().postDataJSON(),
            dataset_ids: [randomUUID()],
          },
        });
        expect(response.status()).toBe(404);
        expect((await response.json()).code).toBe("not_found");
        await route.fulfill({ response });
      },
      { times: 1 },
    );
    const denied = page.waitForResponse(
      (response) => new URL(response.url()).pathname === downloadPath(left.id),
    );
    const beforeDenied = requests.length;
    await button.click();
    expect((await denied).status()).toBe(404);
    await page.waitForTimeout(500);
    expect(requests).toHaveLength(beforeDenied);
    await expect(page.getByText(expiredMessage, { exact: true })).toHaveCount(
      0,
    );

    await actor.api.delete(deletePath(oldId));
    const missed = page.waitForResponse(
      (response) => new URL(response.url()).pathname === downloadPath(left.id),
    );
    const rebuilding = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === comparePath(left.id) &&
        response.request().method() === "POST",
    );
    await button.click();
    const missing = await missed;
    expect(missing.status()).toBe(404);
    const message =
      "This comparison was not found or has expired. Open the comparison again.";
    expect(await missing.json()).toEqual({
      status: false,
      type: "not_found",
      code: "compare_not_found",
      detail: message,
      message,
      error: message,
      result: message,
    });
    expect(missing.request().postDataJSON()).toEqual({
      dataset_ids: [right.id],
      base_column_name: "input",
      compare_id: oldId,
    });
    await expect(page.getByText(expiredMessage, { exact: true })).toBeVisible();
    const response = await rebuilding;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON().compare_id).toBeNull();
    const rebuilt: ComparePage = await response.json();
    const newId = rebuilt.result.metadata.compare_id;
    expect(newId).not.toBe(oldId);
    expect(rebuilt.result.metadata.total_rows).toBe(32);
    const rowZero = grid.locator(
      '.ag-center-cols-container .ag-row[row-index="0"]',
    );
    const transitionalRowZero = await rowZero.evaluateAll((elements) =>
      elements.map((element) => ({
        id: element.getAttribute("row-id"),
        classes: element.className,
      })),
    );
    const newRowId = rebuilt.result.table[0].row_id as string;
    const oldRowIds = first.result.table.map((row) => row.row_id as string);
    expect(oldRowIds).not.toContain(newRowId);
    for (const row of transitionalRowZero) {
      if (row.id && row.id !== newRowId) {
        expect(oldRowIds).toContain(row.id);
        expect(row.classes.split(" ")).toContain("ag-opacity-zero");
      }
    }
    // AG Grid 35 keeps destroyed row controllers as fading "zombies" for
    // ROW_ANIMATION_TIMEOUT (400ms). R3 failed strict lookup during that fade.
    // Poll the UNFILTERED identity set: do not hide clones with first()/nth(),
    // :not(.ag-opacity-zero), or a new-ID-only locator. Old rows must disappear.
    await expect
      .poll(
        () =>
          rowZero.evaluateAll((elements) =>
            elements.map((element) => element.getAttribute("row-id")),
          ),
        { timeout: UI_READY },
      )
      .toEqual([newRowId]);
    for (const id of oldRowIds) {
      await expect(
        grid.locator(`.ag-center-cols-container .ag-row[row-id="${id}"]`),
      ).toHaveCount(0, { timeout: UI_READY });
    }
    await page.waitForTimeout(500); // Observe beyond AG Grid's 400ms fade removal.
    const stableRowZero = await rowZero.evaluateAll((elements) =>
      elements.map((element) => element.getAttribute("row-id")),
    );
    expect(stableRowZero).toEqual([newRowId]);
    await expect(rowZero).toHaveAttribute("row-id", newRowId, {
      timeout: UI_READY,
    });
    for (const dataset of [left, right]) {
      const row = rebuilt.result.table[0];
      const source = dataset.rows.find(
        (item) => item.input === cellValue(row, left.columns[0].id),
      )!;
      expect(cellValue(row, dataset.columns[1].id)).toBe(source.output);
      await expect(
        rowZero.locator(`[col-id="${dataset.columns[1].id}"]`),
      ).toHaveText(source.output, { timeout: UI_READY });
    }
    expect(requests).toHaveLength(beforeDenied + 1); // Exactly one fresh build.
    expect(downloads).toHaveLength(2); // Generic failure + typed miss; never an automatic CSV retry.
    const csvResponse = page.waitForResponse(
      (response) => new URL(response.url()).pathname === downloadPath(left.id),
    );
    const downloaded = page.waitForEvent("download");
    await button.click();
    const csv = await csvResponse;
    expect(csv.status()).toBe(200);
    expect(csv.request().postDataJSON().compare_id).toBe(newId);
    expect((await downloaded).suggestedFilename()).toBe(
      `dataset-${left.id}.csv`,
    );
    const text = await csv.text();
    for (const row of left.rows) expect(text).toContain(row.output);
    for (const row of right.rows) expect(text).toContain(row.output);
    expect(downloads).toHaveLength(3);
    expect(requests).toHaveLength(beforeDenied + 1);
    await info.attach("download-rebuilt-comparison", {
      contentType: "application/json",
      body: JSON.stringify({
        oldId,
        newId,
        rebuilt,
        transitionalRowZero,
        stableRowZero,
        oldRowIds,
        comparisonPostCount: requests.length,
        downloadCount: downloads.length,
      }),
    });
  },
);
