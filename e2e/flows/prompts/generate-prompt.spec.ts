import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const UI_READY = 60_000;

// This flow drives the Generate Prompt drawer over the WS /ws/prompt-stream/
// endpoint (GeneratePromptDrawer.jsx) — it never touches run_template for the
// generation itself, only for the auto-save that lands the applied text.

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
interface SnapshotMessage {
  role: string;
  content: Array<{ type: string; text?: string }>;
}
interface VersionRow {
  prompt_config_snapshot: { messages: SnapshotMessage[] };
  is_draft: boolean;
}

function userText(row: VersionRow): string {
  const user = row.prompt_config_snapshot.messages.find((m) => m.role === 'user');
  return (user?.content ?? [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text ?? '')
    .join('');
}

test(
  'PROMPT-E2E-030: generating a prompt from the Generate drawer applies the streamed text and auto-saves it as a draft',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-030',
      area: 'prompts',
      userGoal:
        'A user describes a task in the Generate Prompt drawer and the AI-generated prompt is applied into the editor without running anything',
      steps: [
        'seed a new draft prompt with an empty user card',
        'open the editor',
        "open the user card's Generate Prompt drawer",
        'type a task statement',
        'click Generate and wait for the WS stream to land the full text',
        'click Continue to apply the generated text into the editor',
      ],
      backendChecks: [
        'the auto-saved draft snapshot (run_template, is_run:false) holds the generated text in the user message',
        'the version remains a draft (is_draft stays true; nothing was committed or run)',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const statement = `e2e-gen-${suffix}`;
    const marker = `e2e-generated-prompt: ${statement}`;

    const draft = await test.step('seed: draft with an empty user card (no model needed — this flow never runs)', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, statement, marker }),
      contentType: 'application/json',
    });

    const userCard = page.locator('[data-testid="prompt-card-1"]');

    // The app-wide notification socket (/ws/connect/ -> sockets.consumer.
    // DataConsumer.connect) never completes its handshake in this stack — it
    // hangs forever inside `channel_layer.group_add` against the RabbitMQ
    // channel layer (verified independently with a raw Node WebSocket:
    // `open` never fires). Chromium won't dispatch a second WebSocket
    // handshake to the same host while that first one is still pending, so
    // without this it silently starves our own /ws/prompt-stream/ connection
    // too — even though that pipeline is proven correct end-to-end outside
    // the browser. This is a pre-existing product/infra defect unrelated to
    // Generate/Improve; stub the socket open (never connect upstream, never
    // close) purely so *this* flow's real WS isn't queued behind it.
    await page.routeWebSocket(/\/ws\/connect\//, () => {});

    await test.step('UI: open the editor and the Generate Prompt drawer', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      await expect(userCard.locator('.ql-editor')).toBeVisible({ timeout: UI_READY });

      // The empty user card only exposes Generate (not Improve —
      // usePromptCardDefaultValues gates on hasContent).
      await userCard.getByRole('button', { name: 'Generate Prompt' }).click();
    });

    // The drawer header flips from "Generate a prompt" to "Your prompt" the
    // instant generation starts, so the filter must match both states.
    const drawer = page.locator('.MuiDrawer-paper').filter({ hasText: /Generate a prompt|Your prompt/ });

    await test.step('UI: type the statement and generate', async () => {
      await expect(drawer.getByPlaceholder('Describe your task...')).toBeVisible({ timeout: UI_READY });
      await drawer.getByPlaceholder('Describe your task...').fill(statement);
      await drawer.getByRole('button', { name: 'Generate', exact: true }).click();

      // Wait for the full streamed text to land (not just a partial chunk)
      // before applying, so a truncated stream fails here instead of
      // silently landing partial text in the editor.
      await expect(drawer.getByText(marker)).toBeVisible({ timeout: UI_READY });
    });

    await test.step('UI: apply the generated prompt into the editor', async () => {
      const autoSaved = page.waitForResponse((r) => {
        if (!r.url().includes(RUN_TEMPLATE_FRAGMENT) || !r.ok()) return false;
        try {
          const posted = r.request().postDataJSON() as {
            is_run?: unknown;
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
          };
          if (posted.is_run) return false;
          const messages = posted.prompt_config?.[0]?.messages ?? [];
          return messages.some((m) => (m.content ?? []).some((c) => (c.text ?? '').includes(marker)));
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

      await drawer.getByRole('button', { name: 'Continue', exact: true }).click();
      await autoSaved;
    });

    await test.step('check 1: the editor shows the generated text', async () => {
      await expect(userCard.locator('.ql-editor')).toContainText(marker, { timeout: UI_READY });
    });

    const version = await test.step('storage: the auto-saved draft version', async () => {
      // No commit_message to anchor on for a draft — the template id plus the
      // newest version is unambiguous (mirrors PROMPT-E2E-017).
      const rows = await probe.pg<VersionRow>(
        `SELECT pv.prompt_config_snapshot, pv.is_draft
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2
          ORDER BY pv.created_at DESC
          LIMIT 1`,
        [draft.root_template, actor.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 2: the draft snapshot holds the generated text', () => {
      expect(userText(version)).toContain(marker);
    });

    await test.step('check 3: the version remains a draft', () => {
      expect(version.is_draft).toBe(true);
    });
  },
);
