import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { attachToCard, MEDIA } from '../../lib/media';

const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const UI_READY = 60_000;

// This flow does NOT run the prompt: running with media attached is blocked
// by an SSRF product gap, so it only proves attach + auto-save on the DRAFT.

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

// The workbench save path serializes an attached image as a content block
// (PromptCards/common.js getBlocks ~line 597-606):
//   { type: "image_url", image_url: { ...imageData, img_name, img_size } }
// imageData already comes off the blot as { url, img_name, img_size }
// (Blots/ImageBlot.jsx static value()); getBlocks then re-reads
// imageObject.imgName / imageObject.imgSize (camelCase, which don't exist on
// that object) and overwrites the correct snake_case fields with undefined,
// which JSON.stringify then drops from the wire body entirely. So the
// persisted block carries only `url` — verified against the actual saved row
// below; only `url` is asserted for that reason.
interface ImageContentBlock {
  type: string;
  image_url?: { url?: string; img_name?: string };
}
interface SnapshotMessage {
  role: string;
  content: ImageContentBlock[];
}
interface VersionRow {
  prompt_config_snapshot: { messages: SnapshotMessage[] };
  is_draft: boolean;
}

test(
  'PROMPT-E2E-017: attaching an image to a draft prompt auto-saves it into the snapshot',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-017',
      area: 'prompts',
      userGoal:
        'A user attaches an image to a draft prompt and it is preserved by the auto-save, without running the prompt',
      steps: [
        'seed a new draft prompt',
        'open the editor',
        'attach an image to the user card',
        'wait for the editor to auto-save the attachment',
        'read the draft version',
      ],
      backendChecks: [
        'the upload-file endpoint returns the uploaded image URL',
        'the auto-saved draft snapshot holds an image_url content block for the uploaded image in the user message',
        'the version remains a draft (is_draft stays true; nothing was committed or run)',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const draft = await test.step('seed: draft (no provider key or model needed — this flow never runs)', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify(draft),
      contentType: 'application/json',
    });

    const uploaded = await test.step('UI: attach an image to the user card', async () => {
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
            prompt_config?: Array<{ messages?: SnapshotMessage[] }>;
          };
          if (posted.is_run) return false;
          const userMessage = posted.prompt_config?.[0]?.messages?.[1];
          return (userMessage?.content ?? []).some((c) => c.type === 'image_url');
        } catch {
          return false;
        }
      }, { timeout: UI_READY });

      const attached = await attachToCard(page, {
        cardIndex: 1,
        kind: 'image',
        file: MEDIA.png,
      });
      await autoSaved;
      return attached;
    });

    await testInfo.attach('uploaded', {
      body: JSON.stringify(uploaded),
      contentType: 'application/json',
    });

    const version = await test.step('storage: the auto-saved draft version', async () => {
      // No commit_message to anchor on for a draft — the template id plus the
      // newest version is unambiguous since this org's draft has exactly one
      // template and the auto-save produces (or updates) its one version row.
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

    const imageBlock = await test.step('check 1: the snapshot holds the uploaded image as an image_url block', () => {
      const userMessage = version.prompt_config_snapshot.messages[1];
      const block = userMessage.content.find((c) => c.type === 'image_url');
      expect(block).toBeDefined();
      expect(block?.image_url?.url).toBe(uploaded.url);
      return block;
    });

    await test.step('check 2: the version remains a draft', () => {
      expect(version.is_draft).toBe(true);
    });
  },
);
