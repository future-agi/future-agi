// Types for hosted-env.mjs (imported by harness/walk-harness.spec.ts).
export interface HostedImages {
  BACKEND_IMAGE: string; BACKEND_DIGEST: string; GATEWAY_IMAGE: string; GATEWAY_DIGEST: string; FRONTEND_IMAGE: string;
}
export const HOSTED_WALK_FIXED: Record<string, string>;
export function fernetKey(): string;
export function requiredNames(compose: string): string[];
export function renderHostedEnv(compose: string, images: HostedImages): Record<string, string>;
export function toEnvFile(env: Record<string, string>): string;
