import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import pg from 'pg';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';

export async function readSkillResult({ apiUrl, headers, path, signal }) {
  const response = await fetch(`${apiUrl}/model-hub/skills/${path}`, { headers, signal });
  requireValue(response.ok, 'Independent public observation unavailable');
  return (await response.json()).result;
}

/** Public-UI driver with independent API/PG witnesses. Synthetic, pre-existing
 * draft/authentication inputs are NOT cold authority setup. Missing target,
 * annotation, role and expiry controls stay failed, never inferred from the
 * passing policy-ABA control or backend unit tests. No competitive claim here.
 * Private VM input: author/reviewer {access,refresh,organizationId,workspaceId,
 * userId,name}, skillId, skillName, baselineVersion, appUrl, apiUrl, pgUrl,
 * outputDir, provenance. Optional evidenceIds identifies user-selected records.
 */
export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J4', 'J4 runs only on the approved cloud Linux VM');
  requireValue(process.env.J4_DRIVER_INPUT, 'Provide a private cloud-local driver input');
  const input = JSON.parse(await readFile(process.env.J4_DRIVER_INPUT, 'utf8'));
  for (const key of ['appUrl', 'apiUrl', 'pgUrl']) requireValue(new URL(input[key]).hostname === 'localhost', 'Use the owned full stack only');
  requireValue(input.author.userId !== input.reviewer.userId, 'Independent author and reviewer required');
  const output = resolve(input.outputDir, runId);
  await mkdir(output, { recursive: true });
  const contexts = [];
  let browser;
  let authorPage;
  let reviewerPage;
  let published;
  let submitted;
  let detail;
  let receipt;
  let baselineObservation;
  let staleObservation;
  let originalRevision;
  const exercised = new Set();
  const db = new pg.Client({ connectionString: input.pgUrl });
  let connected = false;
  const sql = async (text, values) => {
    if (!connected) { await db.connect(); connected = true; }
    return (await db.query(text, values)).rows;
  };
  const headers = (actor) => ({ Authorization: `Bearer ${actor.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId });
  const read = (path, signal, actor = input.author) => readSkillResult({ apiUrl: input.apiUrl, headers: headers(actor), path, signal });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const open = async (actor) => {
    if (!browser) browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 }, recordVideo: { dir: resolve(output, 'video'), size: { width: 1440, height: 900 } } });
    contexts.push(context);
    await context.addInitScript((user) => {
      localStorage.setItem('accessToken', user.access);
      localStorage.setItem('refreshToken', user.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', user.organizationId);
      sessionStorage.setItem('workspaceId', user.workspaceId);
      sessionStorage.setItem('workspaceOrgId', user.organizationId);
    }, actor);
    const page = await context.newPage();
    page.setDefaultTimeout(30_000);
    return page;
  };
  const ack = (page) => page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
  const responseFor = (page, suffix, method = 'POST') => {
    const pending = page.waitForResponse(res => res.url().endsWith(suffix) && res.request().method() === method);
    // Attach rejection handling immediately: a blocked click can outlive the
    // response waiter. The awaited promise still rejects and fails the stage.
    pending.catch(() => {});
    return pending;
  };
  const decisionFor = (value) => ({ packet_sha256: digest(value.packet), preconditions_sha256: digest({ fingerprint: value.fingerprint, revision: value.revision }) });
  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const actor = ['publish-candidate', 'share-and-submit'].includes(stage.id) ? 'author' : 'reviewer';
      const op = new MeasuredOperator({ ledger, actor, step: stage.id, phase: stage.phase, signal });
      const navigate = (page, target, url) => op.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor, step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
      const choose = async (page, label, option, target, operator = op) => {
        await operator.click(page.getByLabel(label), `${target}-open`);
        await operator.click(page.getByRole('option', { name: option, exact: typeof option === 'string' }), `${target}-select`);
      };
      const stamp = async (name, value, decision = undefined, source = 'api') => {
        const artifactHash = await record(name, value);
        ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash, actionSequences: [], ...(decision ? { decision } : {}) });
      };
      const cancel = () => { void browser?.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      try {
        if (stage.id === 'configure-review-authority') {
          throw new Error('Cold authority configuration requires a public role/target setup journey; preconfigured fixture identities do not satisfy it.');
        }
        if (!authorPage) authorPage = await open(input.author);
        if (stage.id === 'publish-candidate') {
          await navigate(authorPage, 'candidate-editor', `/dashboard/skills/${input.skillId}/edit`);
          await op.transition('publication-review', () => op.click(authorPage.getByRole('button', { name: 'Publish', exact: true }), 'open-publication-review'));
          const dialog = authorPage.getByRole('dialog');
          await expect(dialog.getByText('Publish skill', { exact: true })).toBeVisible();
          const draft = await read(`${input.skillId}/draft/`, signal);
          for (const [index, file] of draft.files.entries()) {
            await op.review('candidate-diff', [`file-${index}`], [], async () => {
              const item = dialog.getByRole('button', { name: new RegExp(file.path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')) });
              if (await item.count()) await op.click(item.first(), 'inspect-candidate-file');
            });
          }
          const response = responseFor(authorPage, `/${input.skillId}/draft/publish/`);
          await op.click(dialog.getByRole('button', { name: 'Publish', exact: true }), 'publish-immutable-candidate');
          const result = await response;
          requireValue(result.status() === 201, 'Candidate publication failed');
          published = (await result.json()).result.version;
          baselineObservation = await sql('SELECT v.number FROM model_hub_hubskilllabel l JOIN model_hub_hubskillversion v ON v.id=l.version_id WHERE l.skill_id=$1 AND l.name=$2', [input.skillId, 'production']);
          requireValue(baselineObservation[0]?.number === input.baselineVersion, 'Publication changed production');
          await stamp('publication-observation', { version: await read(`${input.skillId}/versions/${published.number}/`, signal), baseline: baselineObservation }, undefined, 'pg');
        } else if (stage.id === 'share-and-submit') {
          await navigate(authorPage, 'author-reviews', '/dashboard/skills/reviews');
          await op.click(authorPage.getByRole('button', { name: 'Request review', exact: true }), 'open-author-composer');
          await choose(authorPage, 'Release resource', input.skillName, 'subject');
          await choose(authorPage, 'Published candidate', new RegExp(`^v${published.number}(?: —|$)`), 'candidate');
          // Selection and sharing are separate measured user decisions. No API
          // submission substitutes for an unavailable composer or evidence row.
          for (const id of input.evidenceIds || []) {
            const checkbox = authorPage.getByRole('checkbox', { name: new RegExp(`evidence ${id}$`) });
            const section = checkbox.locator('xpath=ancestor::*[.//button[normalize-space()="Share evidence"]][1]');
            if (await section.count()) {
              await op.click(section.getByRole('button', { name: 'Share evidence', exact: true }), 'share-selected-evidence');
              await op.click(authorPage.getByRole('dialog').getByRole('button', { name: 'Share with workspace', exact: true }), 'confirm-evidence-sharing');
            }
            await op.click(checkbox, 'attach-selected-evidence');
          }
          await op.click(authorPage.getByRole('button', { name: 'Prepare exact review', exact: true }), 'prepare-exact-packet');
          await choose(authorPage, 'Assigned reviewer', input.reviewer.name, 'reviewer');
          await op.review('author-review', ['candidate', 'transition', ...(input.evidenceIds || []).map((_, i) => `evidence-${i}`)], [], () => expect(ack(authorPage)).toBeEnabled());
          await op.click(ack(authorPage), 'acknowledge-author-packet');
          const response = responseFor(authorPage, '/release-requests/');
          await op.handoff('assigned-reviewer', () => op.click(authorPage.getByRole('button', { name: 'Send review request', exact: true }), 'submit-exact-review-packet'));
          const sent = await response;
          requireValue(sent.status() === 201, 'Author submission failed');
          submitted = (await sent.json()).result;
          originalRevision = submitted.revision;
          detail = await read(`release-requests/${submitted.id}/`, signal);
          requireValue(detail.author_id === input.author.userId && detail.packet.candidate_version_id === published.id, 'Submitted identity differs');
          await stamp('author-submission', detail);
        } else if (stage.id === 'reviewer-inbox-arrival') {
          reviewerPage = await open(input.reviewer);
          await navigate(reviewerPage, 'reviewer-skills', '/dashboard/skills');
          await op.transition('reviewer-inbox', () => op.click(reviewerPage.getByRole('tab', { name: 'Reviews', exact: true }), 'open-reviews'));
          await op.transition('assigned-request', () => op.click(reviewerPage.getByRole('button', { name: input.skillName, exact: true }), 'open-assigned-packet'));
          const inbox = await read('review-inbox/', signal, input.reviewer);
          requireValue(inbox.results.some(row => row.id === submitted.id && row.unread), 'Assigned unread request absent');
          await stamp('reviewer-arrival', inbox);
        } else if (stage.id === 'review-packet') {
          detail = await read(`release-requests/${submitted.id}/`, signal, input.reviewer);
          const decision = decisionFor(detail);
          const comparison = await read(`${input.skillId}/compare/?from=${input.baselineVersion}&to=${published.number}`, signal, input.reviewer);
          requireValue(comparison.from === input.baselineVersion && comparison.to === published.number, 'Comparison is not the exact release transition');
          const files = comparison.files.filter(file => file.status !== 'unchanged');
          requireValue(files.length > 0, 'Synthetic review must contain an actual changed file');
          for (const [index, file] of files.entries()) {
            // Typography renders the summary itself; use its exact text locator.
            const heading = reviewerPage.getByText(`${file.path} · ${file.status}`, { exact: true });
            const disclosure = heading.locator('..');
            if (await disclosure.getAttribute('open') === null) await op.click(heading, 'open-exact-file-diff');
            await op.review('inspect-exact-file-diff', [`file-${index}`], [], async () => {
              await expect(disclosure.getByRole('table', { name: 'File changes', exact: true })).toBeVisible();
              for (const [side, text] of [['Before', file.from_text], ['After', file.to_text]]) {
                requireValue(typeof text === 'string', 'Independent comparison lacks complete file bytes');
                const cells = disclosure.getByRole('cell', { name: new RegExp(`^${side} line `) }).locator('code');
                requireValue((await cells.allTextContents()).join('\n') === text.replace(/\n$/, ''), 'Visible file diff differs from independently observed bytes');
              }
            }, decision);
          }
          const baselineVersion = await read(`${input.skillId}/versions/${input.baselineVersion}/`, signal, input.reviewer);
          await op.review('inspect-capability-identities', ['baseline-capabilities', 'candidate-capabilities'], [], async () => {
            await expect(reviewerPage.getByText(`Before ${baselineVersion.digest} · candidate ${published.digest}`, { exact: true })).toBeVisible();
            await expect(reviewerPage.getByText('Capability comparison unavailable. No safety conclusion can be drawn.', { exact: true })).not.toBeVisible();
          }, decision);
          const sections = ['Exact release', 'Execution-related changes', 'Static checks', 'Evidence', 'Retained regression pipeline', 'Known impact'];
          const exceptions = [...(!detail.packet.evidence.length ? ['not-tested'] : []), ...(!detail.packet.target ? ['no-application-target'] : [])];
          await op.review('inspect-release-consequences', [...sections.map(item => item.replaceAll(' ', '-').toLowerCase()), ...exceptions], exceptions, async () => {
            for (const item of sections) await expect(reviewerPage.getByText(item, { exact: true }).first()).toBeVisible();
            if (!detail.packet.evidence.length) await expect(reviewerPage.getByText('No evidence selected. Not tested is not a pass.', { exact: true })).toBeVisible();
            if (!detail.packet.target) await expect(reviewerPage.getByText('No application target selected. Native applicability cannot be established.', { exact: true })).toBeVisible();
          }, decision);
          await op.click(ack(reviewerPage), 'acknowledge-reviewed-packet');
          // Item exposure is machine-observed; it is not evidence of human
          // comprehension or of speed. Missing native outcomes remain unqualified.
          await stamp('reviewed-packet', { request: detail, exposure: { files: files.map(file => file.path), capabilities: { beforeDigest: baselineVersion.digest, candidateDigest: published.digest }, exceptions, humanComprehensionMeasured: false } }, decision);
        } else if (stage.id === 'record-decision') {
          const decision = decisionFor(detail);
          const response = responseFor(reviewerPage, `/release-requests/${submitted.id}/decisions/`);
          await op.approve('approve-exact-packet', ['release-packet'], () => reviewerPage.getByRole('button', { name: 'Approve exact packet', exact: true }).click(), decision);
          requireValue((await response).ok(), 'Reviewer decision rejected');
          const decisions = await sql('SELECT d.actor_id::text, p.fingerprint, p.revision FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=$1', [submitted.id]);
          requireValue(decisions.length === 1 && decisions[0].actor_id === input.reviewer.userId && decisions[0].fingerprint === detail.fingerprint, 'Independent decision attribution differs');
          await stamp('recorded-decision', decisions, decision, 'pg');
        } else if (stage.id === 'stale-execution-control') {
          await expect(ack(reviewerPage)).not.toBeChecked();
          await op.click(ack(reviewerPage), 'acknowledge-stale-control');
          await op.click(reviewerPage.getByRole('button', { name: 'Release approved packet', exact: true }), 'prepare-release-control');
          // Use real admin UI in another page; retain the old reviewed packet.
          const policyPage = await reviewerPage.context().newPage();
          const policy = await read(`${input.skillId}/label-protection/`, signal, input.reviewer);
          await navigate(policyPage, 'policy-configuration', `/dashboard/skills/${input.skillId}?tab=versions`);
          if (await policyPage.getByRole('tab', { name: 'Versions', exact: true }).count()) await op.click(policyPage.getByRole('tab', { name: 'Versions', exact: true }), 'open-versions');
          for (const names of [[...policy.protected_label_names, 'j4-guard'], policy.protected_label_names]) {
            await op.click(policyPage.getByRole('button', { name: 'Protection', exact: true }), 'open-label-protection');
            await op.fill(policyPage.getByLabel('Protected label names'), names.join(', '), 'change-protected-names');
            const changed = responseFor(policyPage, '/label-protection/', 'PUT');
            await op.click(policyPage.getByRole('button', { name: 'Save protection', exact: true }), 'save-label-protection');
            requireValue((await changed).ok(), 'Policy control not applied');
            await expect(policyPage.getByRole('dialog')).not.toBeVisible();
            await navigate(policyPage, 'reload-policy-state', `/dashboard/skills/${input.skillId}?tab=versions`);
          }
          await policyPage.close();
          const response = responseFor(reviewerPage, `/release-requests/${submitted.id}/execute/`);
          await op.click(reviewerPage.getByRole('button', { name: 'Move production', exact: true }), 'attempt-stale-release');
          const result = await response;
          staleObservation = { status: result.status(), events: await sql('SELECT id FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=$2', [input.skillId, published.number]) };
          requireValue(staleObservation.status === 409 && staleObservation.events.length === 0, 'Stale approval executed');
          exercised.add('policy-aba'); ledger.fault('policy-aba', 'policy-aba-current-packet');
          await stamp('policy-aba-refusal', staleObservation, undefined, 'pg');
        } else if (stage.id === 'renewed-review') {
          const authorOp = new MeasuredOperator({ ledger, actor: 'author', step: stage.id, phase: stage.phase, signal });
          await authorOp.click(authorPage.getByRole('button', { name: input.skillName, exact: true }), 'open-authored-renewal');
          await choose(authorPage, 'Published candidate', new RegExp(`^v${published.number}(?: —|$)`), 'renewed-candidate', authorOp);
          const authorPreviewResponse = responseFor(authorPage, '/release-requests/preview/');
          await authorOp.click(authorPage.getByRole('button', { name: 'Prepare exact review', exact: true }), 'prepare-renewed-packet');
          const authorPreview = (await (await authorPreviewResponse).json()).result;
          await choose(authorPage, 'Assigned reviewer', input.reviewer.name, 'renewed-reviewer', authorOp);
          await authorOp.review('author-renewal', ['candidate', 'policy-change'], [], () => expect(ack(authorPage)).toBeEnabled(), decisionFor({ ...authorPreview, revision: originalRevision + 1 }));
          await authorOp.click(ack(authorPage), 'acknowledge-renewed-author-packet');
          const sent = responseFor(authorPage, '/release-requests/');
          await authorOp.handoff('assigned-reviewer', () => authorOp.click(authorPage.getByRole('button', { name: 'Send review request', exact: true }), 'submit-renewed-packet'));
          requireValue((await sent).status() === 201, 'Renewal rejected');
          await navigate(reviewerPage, 'renewed-inbox', '/dashboard/skills/reviews');
          await op.click(reviewerPage.getByRole('button', { name: input.skillName, exact: true }), 'open-renewed-packet');
          detail = await read(`release-requests/${submitted.id}/`, signal, input.reviewer);
          requireValue(detail.revision === originalRevision + 1 && detail.status === 'pending', 'Renewal retained old approval');
          const decision = decisionFor(detail);
          await op.review('renewed-packet-review', ['candidate', 'changed-policy', 'transition'], [], () => expect(ack(reviewerPage)).toBeEnabled(), decision);
          await op.click(ack(reviewerPage), 'acknowledge-renewed-review');
          const response = responseFor(reviewerPage, `/release-requests/${submitted.id}/decisions/`);
          await op.approve('approve-renewed-packet', ['release-packet'], () => reviewerPage.getByRole('button', { name: 'Approve exact packet', exact: true }).click(), decision);
          requireValue((await response).ok(), 'Renewed decision rejected');
          await stamp('renewed-review', await read(`release-requests/${submitted.id}/`, signal, input.reviewer), decision);
        } else if (stage.id === 'authorized-release') {
          await expect(ack(reviewerPage)).not.toBeChecked();
          await op.click(ack(reviewerPage), 'acknowledge-final-release');
          await op.click(reviewerPage.getByRole('button', { name: 'Release approved packet', exact: true }), 'prepare-authorized-release');
          const decision = decisionFor(detail);
          const response = responseFor(reviewerPage, `/release-requests/${submitted.id}/execute/`);
          await op.approve('authorize-exact-transition', ['release-packet'], () => reviewerPage.getByRole('button', { name: 'Move production', exact: true }).click(), decision);
          const result = await response;
          requireValue(result.ok(), 'Authorized release rejected');
          receipt = (await result.json()).result;
          await stamp('authorized-release', receipt, decision);
        } else if (stage.id === 'inspect-receipt') {
          await op.transition('durable-receipt', () => ledger.measure({ dimension: 'A', primitive: 'click', actor, step: stage.id, phase: stage.phase, target: 'reopen-durable-receipt' }, () => reviewerPage.reload()));
          await op.review('pre-and-post-move', ['pre-move-currentness', 'post-move-pointer'], [], async () => {
            await expect(reviewerPage.getByText('Durable release receipt', { exact: true })).toBeVisible();
            await expect(reviewerPage.getByText('Currentness before the move', { exact: true })).toBeVisible();
          });
          const durable = await read(`release-requests/${submitted.id}/receipt/`, signal, input.reviewer);
          requireValue(digest(durable) === digest(receipt), 'Durable receipt differs');
          await stamp('durable-receipt', durable);
        } else throw new Error('Unimplemented registered stage');
        await (actor === 'author' ? authorPage : reviewerPage)?.screenshot({ path: resolve(output, `${stage.id}.png`) });
      } catch (error) {
        for (const [index, context] of contexts.entries()) for (const [pageIndex, page] of context.pages().entries()) await page.screenshot({ path: resolve(output, `blocked-${index}-${pageIndex}.png`) }).catch(() => {});
        await record('blocked-stage', { stage: stage.id, reason: error.message, completeJobQualified: false });
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      let source = 'pg'; let raw; let value;
      if (check.id === 'inbox-recipient-set') {
        raw = await sql('SELECT recipient_id::text FROM model_hub_hubskillreleaserecipient WHERE request_id=$1', [submitted.id]);
        value = raw.map(row => row.recipient_id);
      } else if (check.id === 'unauthorized-unread-count') {
        source = 'api'; raw = await read('review-inbox/', signal); value = raw.unread_count;
      } else if (check.id === 'decision-actor-packet') {
        raw = await sql('SELECT d.actor_id::text, p.fingerprint FROM model_hub_hubskillreleasedecision d JOIN model_hub_hubskillreleasepacket p ON p.id=d.packet_id WHERE p.request_id=$1 AND p.revision=$2', [submitted.id, detail.revision]);
        value = raw.length === 1 && raw[0].actor_id === input.reviewer.userId && raw[0].fingerprint === detail.fingerprint;
      } else if (check.id === 'stale-release-refused') {
        raw = { policy: staleObservation, exercised: [...exercised], missing: registration.fault_ids.filter(id => !exercised.has(id)) };
        value = raw.missing.length === 0 && staleObservation.status === 409 && staleObservation.events.length === 0;
      } else if (check.id === 'exact-release-event') {
        raw = await sql('SELECT id::text, from_version, to_version, actor_id::text FROM model_hub_hubskilllabelevent WHERE skill_id=$1 AND to_version=$2', [input.skillId, published.number]);
        value = raw.length === 1 && raw[0].id === receipt.post_move_state.event_id && raw[0].actor_id === input.reviewer.userId && raw[0].from_version === input.baselineVersion;
      } else if (check.id === 'pre-move-currentness') {
        raw = await sql('SELECT response FROM model_hub_hubskillcontextoperation WHERE result_resource_id=$1', [submitted.id]);
        value = raw.some(row => row.response.currentness_at_action?.packet_fingerprint === detail.fingerprint && row.response.currentness_at_action.release_revision < row.response.post_move_state.release_revision);
      } else if (check.id === 'production-unchanged-before-release') {
        raw = baselineObservation; value = raw?.length === 1 && raw[0].number === input.baselineVersion;
      } else throw new Error('Unobserved check');
      return { source, value, artifact_sha256: await record(`oracle-${check.id}`, raw) };
    },
    async teardown() {
      for (const context of contexts) await context.close();
      await browser?.close();
      if (connected) await db.end();
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}
