import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedMockCustomModel, selectModel } from '../../lib/models';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt);
// same set PROMPT-E2E-006 uses, plus the schema endpoint this flow adds:
//   responseSchema -> POST/GET /model-hub/response_schema/  (CreateResponseSchema.jsx)
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
const RESPONSE_SCHEMA_FRAGMENT = '/response_schema/';

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
interface VersionRow {
  prompt_config_snapshot: { configuration?: { response_format?: unknown } };
  commit_message: string;
}
interface SchemaRow {
  id: string;
}

test(
  'PROMPT-E2E-024: a custom response schema created in the editor is committed as the response format',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-024',
      area: 'prompts',
      userGoal:
        'A user creates a custom response schema from the response-format control and selects it, and the version committed afterward records that schema as the response format',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the editor, author a user message, select the model',
        'open the response-format control and click "Create custom schema"',
        'name the schema and save it, selecting it as the response format',
        'run the prompt, then commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'a model_hub_userresponseschema row exists with the minted name, scoped to the organization',
        'prompt_config_snapshot.configuration.response_format on the committed version equals that schema row\'s id',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-user-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-${suffix}`;
    const schemaName = `e2e-schema-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      await seedMockCustomModel(actor.api, modelName);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText, modelName, commitMsg, schemaName }),
      contentType: 'application/json',
    });

    let schemaId = '';

    await test.step('UI: author, select the model, and create a custom schema', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });

      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: Array<{ content?: Array<{ text?: string }> }> }>;
          };
          if (posted.is_run) return false;
          const messages = posted.prompt_config?.[0]?.messages ?? [];
          return messages.some((m) =>
            (m.content ?? []).some((c) => (c.text ?? '').includes(userText)),
          );
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

      await userEditor.click();
      await page.keyboard.type(userText);
      await autoSaved;

      await selectModel(page, modelName);

      // ResponseFormatSelector.jsx (Playground/ModelContainer.jsx) renders
      // the trigger as a plain Box (no role/test id) showing the currently
      // selected format's label; a freshly-selected chat model defaults to
      // "Text output". Click it by its visible, unique label text.
      const trigger = page.getByText('Text output', { exact: true });
      await expect(trigger).toBeVisible({ timeout: UI_READY });
      await trigger.click();

      await page
        .getByRole('menuitem')
        .filter({ hasText: 'Create custom schema' })
        .click();

      // CreateResponseSchema.jsx (custom-model-options/CreateResponseSchema.jsx)
      // opens a real MUI Dialog titled "Create new schema" with a
      // pre-filled default JSON schema — only Name/Description are
      // required to make the form valid, so the JSON editor is left as-is.
      const schemaDialog = page.getByRole('dialog').filter({ hasText: 'Create new schema' });
      await expect(schemaDialog).toBeVisible({ timeout: UI_READY });
      await schemaDialog.getByPlaceholder('Enter name').fill(schemaName);
      await schemaDialog.getByPlaceholder('Enter description').fill(`desc-${suffix}`);

      const schemaCreated = page.waitForResponse(
        (r) =>
          r.url().includes(RESPONSE_SCHEMA_FRAGMENT) &&
          r.request().method() === 'POST' &&
          r.ok(),
        { timeout: UI_READY },
      );
      await schemaDialog.getByRole('button', { name: 'Save', exact: true }).click();
      const schemaResponse = await schemaCreated;
      const schemaBody = (await schemaResponse.json()) as { id?: string; result?: { id?: string } };
      schemaId = schemaBody.id ?? schemaBody.result?.id ?? '';

      // The trigger now shows the schema's own name as the selected format.
      // (A closing Menu can still hold a matching MenuItem in the DOM
      // during its exit transition, so anchor on the first match rather
      // than asserting uniqueness here — this is a soft UI confirmation,
      // not the flow's backend-verified check.)
      await expect(page.getByText(schemaName, { exact: true }).first()).toBeVisible({
        timeout: UI_READY,
      });
    });

    await test.step('UI: run (with the schema selected) and commit', async () => {
      // run_template's litellm call (model_hub/views/prompt_template.py
      // ~L1953) passes configuration.response_format straight through to
      // litellm without resolving a custom-schema id to its stored JSON
      // schema first — unlike the dataset-batch-run path
      // (views/run_prompt.py ~L1909-1916, which does
      // `UserResponseSchema.objects.get(id=rf).schema` before calling
      // RunPrompt). litellm then rejects the bare uuid with "Invalid
      // response_format '<uuid>'. Must be {'type': 'text'} or {'type':
      // 'json_object'}", so a run made with a custom schema selected always
      // errors from the model call — a backend gap, not a test bug.
      //
      // That error is immaterial to Commit's gate, though:
      // WorkbenchProvider.jsx saveAndRun flips selectedVersions[i].isDraft
      // to false *synchronously on click*, before the network call even
      // starts, and PromptActions.jsx's disableCommit reads only that flag.
      // So Run unlocks Commit regardless of whether the run itself
      // succeeds; wait for the (erroring) run_template response to settle
      // the UI, then commit — the committed snapshot still carries
      // whatever response_format was configured at commit time.
      const ran = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT)) return false;
        try {
          return Boolean((r.request().postDataJSON() as { is_run?: unknown }).is_run);
        } catch {
          return false;
        }
      }, { timeout: UI_READY });
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      await ran;

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
        `SELECT pv.prompt_config_snapshot, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    // Read the schema row itself by the minted name — not just the id the
    // create POST happened to return — so the check proves "the schema
    // named e2e-schema-<suffix> is what got committed", not merely
    // "whatever id the POST returned got committed". UserResponseSchema
    // (model_hub/models/run_prompt.py ~L317) -> default table
    // model_hub_userresponseschema, organization-scoped.
    const schema = await test.step('storage: the response_schema row', async () => {
      const rows = await probe.pg<SchemaRow>(
        `SELECT id FROM model_hub_userresponseschema
          WHERE name = $1 AND organization_id = $2`,
        [schemaName, actor.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the committed response_format is the minted schema\'s id', () => {
      expect(schema.id).toBe(schemaId);
      expect(version.prompt_config_snapshot.configuration?.response_format).toBe(schema.id);
    });
  },
);
