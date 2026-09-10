import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned from frontend/src/utils/axios.js (endpoints.develop.runPrompt):
//   createPromptDraft -> /model-hub/prompt-templates/create-draft/
//   runTemplatePrompt -> /model-hub/prompt-templates/{id}/run_template/  (auto-save posts here with is_run:false)
//   commitSavePrompt  -> /model-hub/prompt-templates/{id}/commit/
const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
// Custom-model seeding — same endpoint and wire body the eval flow uses
// (e2e/flows/evals/eval-task.spec.ts), which already proves this path runs a
// live completion against the mock before it persists.
const CUSTOM_MODEL_PATH = '/model-hub/custom_models/create/';

// The editor gates Commit behind a *completing* run (PromptActions.jsx:316-320,
// tooltip "Please run the prompt before saving and committing"; isDraft only
// clears via a run, WorkbenchProvider.jsx:1565-1602), and Run needs a selected
// model with a working provider. A fresh org has none, so we seed a custom model
// pointed straight at the mock. In the managed stack the mock is reachable in the
// compose network at mock-llm:8080; the dev-attach spike overrides this to the
// host mock. The mock ignores the model field, so the name can be unique — which
// lets the picker option be selected by its text with no test-id.
const MOCK_LLM_BASE = process.env.E2E_MOCK_LLM_BASE ?? 'http://mock-llm:8080/v1';
const MOCK_LLM_KEY = 'local-dev-only-shared-secret-replace-me';

// Browser-side waits. The local stack slows several-fold under parallel specs,
// so the 10s expect default is not a first-paint budget; sized like the observe
// flows' own constant. The run is WS-first with a 10s fallback to HTTP polling
// (PROMPT_RUN_SOCKET_FALLBACK_MS), so the run wait has to clear that too.
const UI_READY = 60_000;

// The create-draft default body. Pinned from frontend/src/sections/workbench/
// constant.js (createDraftPayload + DefaultMessages): a new draft is seeded with
// exactly two empty cards — a system card at index 0 and a user card at index 1.
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

// One authored message as stored in prompt_config_snapshot.messages. Pinned from
// futureagi/model_hub/views/prompt_template.py: the workbench save path writes
// content as a list of {type:"text", text} blocks (dummy shape lines 754-779).
interface SnapshotMessage {
  role: string;
  content: Array<{ type: string; text?: string }>;
}
interface PromptConfigSnapshot {
  messages: SnapshotMessage[];
  configuration?: { model?: string } & Record<string, unknown>;
}
interface VersionRow {
  prompt_config_snapshot: PromptConfigSnapshot;
  is_draft: boolean;
  commit_message: string;
  is_default: boolean;
  template_version: string;
  output: unknown;
}

// The workbench stores content as {type:"text", text} blocks and Quill always
// keeps a trailing "\n" in its document, which getBlocks() carries into the
// saved text. Normalise the trailing newline on the RECEIVED side only (a Quill
// artifact, not authored content), the same way PromptEditor.jsx does when it
// diffs its own content, then compare exactly.
function messageText(msg: SnapshotMessage): string {
  return (msg.content ?? [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text ?? '')
    .join('')
    .replace(/\n+$/, '');
}

test(
  'PROMPT-E2E-006: a prompt authored in the workbench editor runs and is committed with exactly what was written',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-006',
      area: 'prompts',
      userGoal:
        'A user authors a prompt in the workbench editor, runs it against a model, and commits it as a version, and the saved version holds exactly what they wrote and ran',
      steps: [
        'seed a new draft prompt and a custom model that reaches the mock',
        'open the workbench editor for the draft',
        'type a system message into the first card',
        'type a user message into the second card',
        'wait for the editor to auto-save the authored content',
        'select the seeded model and run the prompt',
        'wait for the run output to appear',
        'open the Commit dialog, enter a commit message, and confirm',
        'read the committed version',
      ],
      backendChecks: [
        'the run returns the mock echo of the authored user message, shown in the output panel',
        'commit finalizes the model_hub_promptversion row: is_draft is false and the commit_message is stored',
        'prompt_config_snapshot.messages holds exactly the authored system + user messages, in order',
        'prompt_config_snapshot.configuration.model is the seeded model',
        'the committed version is not marked default (committed without set-default)',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Navigation, typing two cards, the auto-save wait, the model pick, the run
    // (incl. the WS->HTTP fallback), and the commit each spend up to a UI_READY;
    // the sum passes the 120s default, and the remainder is headroom so a slow
    // run fails on the assertion that ran out rather than the outer timeout.
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const sysText = `e2e-sys-${suffix}`;
    const userText = `e2e-user-${suffix}`;
    const modelName = `e2e-mock-${suffix}`;
    const commitMsg = `e2e-commit-${suffix}`;

    const draft = await test.step('seed: create a new draft and a mock-backed model', async () => {
      const body = await actor.api.post<DraftEnvelope>(
        CREATE_DRAFT_PATH,
        CREATE_DRAFT_BODY,
      );
      // Creating the custom model runs a live completion against api_base before
      // it persists, so a 2xx here already proves the backend can reach the mock.
      await actor.api.post(CUSTOM_MODEL_PATH, {
        model_provider: 'openai',
        model_name: modelName,
        input_token_cost: 0,
        output_token_cost: 0,
        config_json: { key: MOCK_LLM_KEY, api_base: MOCK_LLM_BASE },
      });
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, sysText, userText, modelName, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: author a system and a user message in the editor', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });

      // PromptCard.jsx tags each card wrapper data-testid="prompt-card-<index>";
      // a fresh draft renders card 0 = system, card 1 = user (constant.js
      // DefaultMessages). The Quill editor mounts a .ql-editor inside each.
      const systemEditor = page.locator(
        '[data-testid="prompt-card-0"] .ql-editor',
      );
      const userEditor = page.locator(
        '[data-testid="prompt-card-1"] .ql-editor',
      );
      await expect(systemEditor).toBeVisible({ timeout: UI_READY });
      await expect(userEditor).toBeVisible({ timeout: UI_READY });

      await systemEditor.click();
      await page.keyboard.type(sysText);
      await userEditor.click();

      // Wait for the debounced auto-save whose posted body already carries the
      // user text: Quill TEXT_CHANGE -> onPromptChange -> a run_template POST
      // with is_run:false. Anchoring on the user text guarantees the save we
      // wait for is the one that captured the final authored state.
      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
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

      await page.keyboard.type(userText);
      await autoSaved;
    });

    await test.step('UI: select the seeded model and run', async () => {
      // A fresh draft has no model, so the first Run click opens the model
      // picker rather than running (PromptActions.jsx:712-726). Filter the list
      // by the seeded model's unique name and pick it, then Run again runs it.
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      const search = page.getByRole('textbox', { name: 'Select model' });
      await expect(search).toBeVisible({ timeout: UI_READY });
      await search.fill(modelName);
      const option = page.getByText(modelName, { exact: true });
      await expect(option).toBeVisible({ timeout: UI_READY });
      await option.click();
      await page.getByRole('button', { name: 'Run Prompt' }).click();
    });

    await test.step('check 1: the run shows the mock echo of the user message', async () => {
      // The mock returns "echo: " + the last user message content. The workbench
      // sends that content as a {type,text} block array, so the echo reads
      //   echo: [{"type":"text","text":"<userText>\n"}]
      // Anchor on the "echo:" output carrying the minted user text, not the exact
      // string, and wait on the output (not the WS) so the WS->HTTP fallback is
      // transparent.
      const runOutput = page.getByText(/^echo:/);
      await expect(runOutput).toBeVisible({ timeout: UI_READY });
      await expect(runOutput).toContainText(userText);
    });

    await test.step('UI: commit the version with a message', async () => {
      // The top-bar Commit opens the dialog; the dialog carries its own Commit /
      // "Commit and set as a default version" pair. Open first, confirm inside.
      await page
        .getByRole('button', { name: 'Commit', exact: true })
        .first()
        .click();

      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog
        .getByPlaceholder('Enter a commit message for this version...')
        .fill(commitMsg);

      const committed = page.waitForResponse(
        (r) => r.url().includes(COMMIT_FRAGMENT) && r.ok(),
        { timeout: UI_READY },
      );
      // exact:true so this matches "Commit", never "Commit and set as a default
      // version" — committing without set-default is what check 5 rests on.
      await dialog.getByRole('button', { name: 'Commit', exact: true }).click();
      await committed;
    });

    const version = await test.step('storage: the committed version', async () => {
      // PromptVersion has no org column; scope through original_template_id to
      // the template's organization_id. Anchor on the minted commit_message so
      // the row is found regardless of which version number it landed on.
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.prompt_config_snapshot, pv.is_draft, pv.commit_message,
                pv.is_default, pv.template_version, pv.output
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
            AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 2: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });

    await test.step('check 3: the snapshot holds exactly the authored messages', () => {
      const messages = version.prompt_config_snapshot.messages;
      expect(messages).toHaveLength(2);
      expect(messages[0].role).toBe('system');
      expect(messageText(messages[0])).toBe(sysText);
      expect(messages[1].role).toBe('user');
      expect(messageText(messages[1])).toBe(userText);
    });

    await test.step('check 4: the snapshot records the seeded model', () => {
      expect(version.prompt_config_snapshot.configuration?.model).toBe(modelName);
    });

    await test.step('check 5: the version is not marked default', () => {
      expect(version.is_default).toBe(false);
    });
  },
);
