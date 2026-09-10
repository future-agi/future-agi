import fs from 'node:fs';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { MEDIA } from '../../lib/media';
import { seedMockCustomModel, selectModel } from '../../lib/models';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const CREATE_DATASET_PATH = '/model-hub/develops/create-dataset-from-local-file/';
const DATASET_PROGRESS_PATH = (id: string) =>
  `/model-hub/develops/dataset-creation-progress/${id}/`;
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';

const UI_READY = 60_000;

const CREATE_DRAFT_BODY = {
  name: '',
  prompt_config: [
    {
      messages: [
        { role: 'system', content: [{ type: 'text', text: '' }] },
        { role: 'user', content: [{ type: 'text', text: '' }] },
      ],
    },
  ],
};

interface DraftEnvelope {
  result: { root_template: string; name: string };
}
interface DatasetCreateEnvelope {
  result: { dataset_id: string; dataset_name: string; processing_status: string };
}
interface DatasetProgressEnvelope {
  result: { is_completed: boolean; is_failed: boolean; error_message: string | null };
}
interface VersionRow {
  variable_names: Record<string, string[]>;
  is_draft: boolean;
  commit_message: string;
}

// variables.csv (e2e/fixtures/media/variables.csv): one column, header "topic",
// no commas in any value, so splitting on lines gives the exact cell values.
const csvLines = fs.readFileSync(MEDIA.csv, 'utf8').split(/\r?\n/).filter(Boolean);
const [csvHeader, ...csvValues] = csvLines;

test(
  'PROMPT-E2E-018: importing a dataset into variables maps a column to the prompt variable',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-018',
      area: 'prompts',
      userGoal:
        'A user imports a dataset into the Variables drawer, maps a CSV column to the prompt variable, runs the prompt once per row, and commits, and the saved version records the imported column values',
      steps: [
        'seed a dataset from a local CSV file and wait for background processing to complete',
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor and type a message containing a {{variable}}',
        'select the model and run, which opens the Variables drawer',
        'open Import Dataset, pick the seeded dataset, map the variable to the CSV column, and Apply',
        'save the imported variable data and run',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the dataset-creation-progress endpoint reaches is_completed once the worker processes the file',
        'the run produces one output per dataset row (two echoes, one per imported CSV value)',
        'the committed version variable_names.topic holds exactly the CSV column values',
        'commit finalizes the version: is_draft is false and the commit_message is stored',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const varName = 'topic';
    const datasetName = `e2e-ds-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-import-${suffix}`;

    const dataset = await test.step('seed: create a dataset from the local CSV', async () => {
      const created = await actor.api.postMultipart<DatasetCreateEnvelope>(
        CREATE_DATASET_PATH,
        {
          file: {
            name: 'variables.csv',
            mimeType: 'text/csv',
            buffer: fs.readFileSync(MEDIA.csv),
          },
          new_dataset_name: datasetName,
          // Dataset.model_type is non-null at the model layer even though the
          // request serializer marks it optional; the UI's own upload modal
          // (UploadFileModal.jsx defaultValues) always sends "GenerativeLLM".
          model_type: 'GenerativeLLM',
        },
      );
      return created.result;
    });

    await testInfo.attach('seeded-dataset', {
      body: JSON.stringify({ dataset, csvHeader, csvValues }),
      contentType: 'application/json',
    });

    await test.step('storage: wait for the worker to finish processing the dataset', async () => {
      let last: DatasetProgressEnvelope['result'] | undefined;
      await expect
        .poll(
          async () => {
            const body = await actor.api.get<DatasetProgressEnvelope>(
              DATASET_PROGRESS_PATH(dataset.dataset_id),
            );
            last = body.result;
            if (body.result.is_failed) {
              throw new Error(
                `dataset processing failed: ${body.result.error_message}`,
              );
            }
            return body.result.is_completed;
          },
          // Celery `tasks_l` queue processing, not the PeerDB CDC mirror — no CH involved.
          { timeout: 180_000, intervals: [2000, 5000] },
        )
        .toBe(true);
      await testInfo.attach('dataset-progress-final', {
        body: JSON.stringify(last),
        contentType: 'application/json',
      });
    });

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await test.step('UI: type a message with a variable and select the model', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const saved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as { is_run?: unknown };
          return !posted.is_run;
        } catch {
          return false;
        }
      }, { timeout: UI_READY });
      await userEditor.click();
      await page.keyboard.type(`about {{${varName}}}`);
      await saved;

      await selectModel(page, modelName);
    });

    await test.step('UI: run opens the Variables drawer, then open Import Dataset', async () => {
      // Running with an undefined variable opens the Variables drawer
      // (PromptActions.jsx:723 setVariableDrawerOpen).
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const importButton = page.getByRole('button', { name: 'open-import-dataset' });
      await expect(importButton).toBeVisible({ timeout: UI_READY });
      await importButton.click();
    });

    await test.step('UI: pick the dataset, map the variable to its column, Apply', async () => {
      const datasetField = page.getByPlaceholder('Select a dataset');
      await expect(datasetField).toBeVisible({ timeout: UI_READY });
      await datasetField.click();
      await datasetField.fill(datasetName);
      await page.getByRole('menuitem', { name: datasetName, exact: true }).click();

      const columnField = page.getByPlaceholder('Select a column');
      await expect(columnField).toBeVisible({ timeout: UI_READY });
      await columnField.click();
      await columnField.fill(varName);
      await page.getByRole('menuitem', { name: varName, exact: true }).click();

      await page.getByRole('button', { name: 'Apply', exact: true }).click();
    });

    await test.step('check 1: the import populated the Variables grid with the CSV values', async () => {
      const cells = page.locator(`.ag-row [col-id="${varName}"]`);
      await expect(cells).toHaveCount(csvValues.length, { timeout: UI_READY });
      for (let i = 0; i < csvValues.length; i++) {
        await expect(cells.nth(i)).toHaveText(csvValues[i]);
      }
    });

    await test.step('UI: save the imported variable data and run', async () => {
      // The drawer's Save button (VariableDrawer.jsx handleSave) writes the grid
      // values into variableData and closes the drawer.
      await page.getByRole('button', { name: 'Save', exact: true }).click();

      // One complete variable row per dataset row -> the run produces a
      // MultiResult, one output per row; the mock echoes each row's
      // substituted message.
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await expect(page.getByText(/^echo:/)).toHaveCount(csvValues.length, {
        timeout: UI_READY,
      });
    });

    await test.step('check 2: each imported row produced its own echoed output', async () => {
      const echoes = page.getByText(/^echo:/);
      for (let i = 0; i < csvValues.length; i++) {
        await expect(echoes.nth(i)).toContainText(csvValues[i]);
      }
    });

    await test.step('UI: commit the version', async () => {
      await page.getByRole('button', { name: 'Commit', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog
        .getByPlaceholder('Enter a commit message for this version...')
        .fill(commitMsg);
      const committed = page.waitForResponse(
        (r) => r.url().includes(COMMIT_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      await dialog.getByRole('button', { name: 'Commit', exact: true }).click();
      await committed;
    });

    const version = await test.step('storage: the committed version', async () => {
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.variable_names, pv.is_draft, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 3: the imported CSV values are saved on the version', () => {
      expect(version.variable_names[varName]).toEqual(csvValues);
    });

    await test.step('check 4: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });
  },
);
