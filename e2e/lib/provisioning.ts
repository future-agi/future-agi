import type { APIRequestContext } from '@playwright/test';
import { ApiClient, Tokens } from './api-client';
import { E2E } from './env';
import { requirePublicationAdmission } from './value-free';
import { mint } from './minted-values';
import { createPersonalKey } from './personal-key';

export interface TestActor {
  email: string; password: string;
  tokens: Tokens;
  organizationId: string; workspaceId: string;
  apiKey: string; secretKey: string;
  api: ApiClient;
}

export interface UserInfo {
  organization: { id: string };
  default_workspace_id: string | null;
  restrict_simulate_to_environments?: boolean;
}

export type BeforePersonalKey = (identity: Omit<TestActor, 'apiKey' | 'secretKey'>) => Promise<void>;

export async function provisionActor(req: APIRequestContext, label: string, beforePersonalKey?: BeforePersonalKey,
  domain = 'futureagi.com'): Promise<TestActor> {
  // HR-JRN-01 structural S3: in value-free mode, nothing is signed up before the publication is admitted (lib/value-free.ts).
  await requirePublicationAdmission('provisionActor');
  // futureagi.com domain: belt (special-email recaptcha bypass) to the
  // localhost-Host suspenders; also gives the auto-created org a stable name.
  // `domain`: another one (HR-JRN-01's value-free ScopeActors, lib/scope-actors.ts);
  // signup names the org after its first label (accounts/utils.py first_signup).
  // Freshness must not depend on the caller's label: parallel workers can
  // provision in the same millisecond.
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const email = `e2e-${label}-${runId}@${domain}`;
  const password = `E2e-${label}-${runId}`;
  const anon = new ApiClient(req, E2E.apiUrl);

  await anon.post('/accounts/signup/', { email, full_name: `E2E ${label}`, password });
  const tokens = await anon.post<Tokens>('/accounts/token/', { email, password, remember_me: true });

  let api = anon.withAuth(tokens);
  let info = await api.get<UserInfo>('/accounts/user-info/');
  if (!info.default_workspace_id) info = await api.get<UserInfo>('/accounts/user-info/');
  if (!info.default_workspace_id) throw new Error(`no default workspace for ${email} — see Task 3 Step 3`);

  const organizationId = info.organization.id;
  const workspaceId = info.default_workspace_id;
  api = anon.withAuth(tokens, organizationId, workspaceId);
  await beforePersonalKey?.({ email, password, tokens, organizationId, workspaceId, api });
  const keys = await createPersonalKey(api, `e2e-${label}-${runId}`);
  // 708 add.78: the evidence auditor matches these exactly (in memory only).
  mint(password, tokens.access, tokens.refresh, keys.api_key, keys.secret_key);

  return { email, password, tokens, organizationId, workspaceId,
           apiKey: keys.api_key, secretKey: keys.secret_key, api };
}
