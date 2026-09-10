import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedOpenAIProviderKey, selectModel } from '../../lib/models';

// BLOCKED: this flow currently fails at "UI: run and wait for the generated
// audio" — every TTS run through the workbench Run Prompt button fails
// server-side with litellm's "'voice' is required to be passed as a string
// for OpenAI TTS", regardless of the voice selected in the UI.
//
// Root cause (verified in the running e2e backend container, not just the
// host checkout): futureagi/model_hub/views/prompt_template.py's
// run_template() action builds the RunPrompt(...) call (~line 1940) from
// explicit configuration fields (model, temperature, response_format, tools,
// output_format, ...) and never passes run_prompt_config=. RunPrompt.__init__
// (agentic_eval/core_evals/run_prompt/litellm_response.py:110-115) therefore
// always sees self.run_prompt_config == {}, so both the legacy
// _speech_response() (same file, ~line 413: `run_prompt_config.get("voice_id")
// or run_prompt_config.get("voice")`) and the new handler path
// (runprompt_handlers/handlers/tts/speech_api_handler.py:78, which reads
// `run_prompt_config=self.context.run_prompt_config`) always resolve voice to
// None, even though the browser does POST a correct
// prompt_config[0].configuration.voice_id (e.g. "alloy", auto-filled by
// ModelContainer.jsx's voiceOptions effect) with the run_template request.
//
// This is a product gap, not a test/harness issue — left un-quarantined per
// instructions. Do not "fix" it by loosening the assertions below; they
// describe the intended, currently-unreachable behavior.


const CREATE_DRAFT_PATH = '/model-hub/prompt-templates/create-draft/';
const RUN_TEMPLATE_FRAGMENT = '/run_template/';
const COMMIT_FRAGMENT = '/commit/';
const UI_READY = 60_000;
const TTS_MODEL = 'gpt-4o-mini-tts';

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
  prompt_config_snapshot: {
    configuration?: {
      model?: string;
      model_detail?: { type?: string };
    };
  };
  metadata: Array<{ usage?: { completion_tokens?: number } } | null> | null;
  output: string[] | null;
  is_draft: boolean;
  commit_message: string;
}

test(
  'PROMPT-E2E-015: a TTS prompt runs against the model and commits with the audio output',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'PROMPT-E2E-015',
      area: 'prompts',
      userGoal:
        'A user writes a TTS prompt, runs it against a text-to-speech model, hears the generated audio, and commits it',
      steps: [
        'seed a new draft prompt and an OpenAI provider key (routed to the mock)',
        'open the editor and type the text to speak',
        'select the TTS model (auto-sets the response format to Audio output)',
        'run and wait for the generated audio player',
        'commit the version',
        'read the committed version',
      ],
      backendChecks: [
        'the run produces a playable audio output in the output panel',
        'the committed snapshot records the TTS model and type tts',
        'the version metadata carries the mock audio usage token count',
        'the output holds the generated audio file URL',
        'commit finalizes the version: is_draft is false and the commit_message is stored',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    test.setTimeout(300_000);

    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const userText = `e2e-tts-${suffix}`;
    const commitMsg = `e2e-ttsc-${suffix}`;

    const draft = await test.step('seed: draft + OpenAI provider key (mock-routed)', async () => {
      const body = await actor.api.post<DraftEnvelope>(CREATE_DRAFT_PATH, CREATE_DRAFT_BODY);
      await seedOpenAIProviderKey(actor.api);
      return body.result;
    });

    await testInfo.attach('seeded', {
      body: JSON.stringify({ ...draft, userText, commitMsg }),
      contentType: 'application/json',
    });

    await test.step('UI: type the text to speak and select the TTS model', async () => {
      await page.goto(`/dashboard/workbench/create/${draft.root_template}`, {
        waitUntil: 'domcontentloaded',
      });
      const userEditor = page.locator('[data-testid="prompt-card-1"] .ql-editor');
      await expect(userEditor).toBeVisible({ timeout: UI_READY });
      const saved = page.waitForResponse((r) => {
        try {
          return r.url().includes(RUN_TEMPLATE_FRAGMENT) && r.ok() &&
            !(r.request().postDataJSON() as { is_run?: unknown }).is_run;
        } catch {
          return false;
        }
      }, { timeout: UI_READY });
      await userEditor.click();
      await page.keyboard.type(userText);
      await saved;

      // Selecting a TTS model kicks off a GET for its voice options
      // (ModelContainer.jsx useVoiceOptions -> /model-hub/api/model_voices/)
      // and auto-fills modelConfig.voiceId from the response once it lands.
      // Run without a voice fails server-side ("'voice' is required..."), so
      // wait for that response before running.
      const voiceOptionsLoaded = page.waitForResponse(
        (r) => r.url().includes('/model-hub/api/model_voices/') && r.ok(),
        { timeout: UI_READY },
      );
      await selectModel(page, TTS_MODEL);
      await voiceOptionsLoaded;
    });

    await test.step('UI: run and wait for the generated audio', async () => {
      await page.getByRole('button', { name: 'Run Prompt' }).click();
      // NOTE (blocked, see bottom of file): TestAudioPlayer.jsx's
      // <IconButton aria-label="play-pause"> mounts for ANY audio-output run,
      // including a failed one whose output text is an error string — the
      // WaveSurfer load only happens lazily on click. It is not a valid
      // run-succeeded signal on its own; once the backend gap below is fixed,
      // anchor the wait on the run_template response body's output instead
      // (mirroring check 3), e.g. output[0] matching /\.wav$|tempcust\//.
      await expect(page.getByRole('button', { name: 'play-pause' })).toBeVisible({
        timeout: UI_READY,
      });
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
        `SELECT pv.prompt_config_snapshot, pv.metadata, pv.output, pv.is_draft, pv.commit_message
           FROM model_hub_promptversion pv
           JOIN model_hub_prompttemplate pt ON pv.original_template_id = pt.id
          WHERE pt.id = $1 AND pt.organization_id = $2 AND pv.commit_message = $3`,
        [draft.root_template, actor.organizationId, commitMsg],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    });

    await test.step('check 1: the snapshot records a TTS model', () => {
      expect(version.prompt_config_snapshot.configuration?.model).toContain('tts');
      expect(version.prompt_config_snapshot.configuration?.model_detail?.type).toBe('tts');
    });

    await test.step('check 2: the metadata carries the mock audio usage token count', () => {
      const tokens = version.metadata?.[0]?.usage?.completion_tokens;
      expect(tokens).toBe(32);
    });

    await test.step('check 3: the output holds the generated audio file', () => {
      const out = version.output?.[0] ?? '';
      expect(out).toMatch(/\.wav$/);
    });

    await test.step('check 4: commit finalized the version', () => {
      expect(version.is_draft).toBe(false);
      expect(version.commit_message).toBe(commitMsg);
    });
  },
);
