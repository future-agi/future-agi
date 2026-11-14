// Runs inside the browser before any app code. Keys pinned from
// frontend/src/auth/context/jwt/utils.js and the proven pattern in
// frontend/scripts/api-journeys/browser/*.mjs.
export interface AuthSeed { access: string; refresh: string; organizationId: string; workspaceId: string }

export function authInitScript(seed: AuthSeed): void {
  localStorage.setItem('accessToken', seed.access);
  localStorage.setItem('refreshToken', seed.refresh);
  localStorage.setItem('rememberMe', 'true');
  sessionStorage.setItem('organizationId', seed.organizationId);
  sessionStorage.setItem('workspaceId', seed.workspaceId);
}
