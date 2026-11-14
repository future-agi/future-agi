import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";

export function configuredTokenFilePath(env = process.env) {
  const rawPath = env.FUTURE_AGI_TOKEN_FILE || env.API_JOURNEY_TOKEN_FILE || "";
  if (!rawPath) return "";
  if (rawPath === "~") return os.homedir();
  if (rawPath.startsWith("~/")) {
    return path.join(os.homedir(), rawPath.slice(2));
  }
  return path.resolve(rawPath);
}

export async function readCachedTokens({ apiBase, env = process.env } = {}) {
  const filePath = configuredTokenFilePath(env);
  if (!filePath) return null;

  let raw;
  try {
    raw = await fs.readFile(filePath, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    return null;
  }

  const access =
    payload?.access || payload?.access_token || payload?.tokens?.access || "";
  if (!access) return null;

  const cachedApiBase = payload?.api_base || payload?.apiBase || "";
  if (
    cachedApiBase &&
    apiBase &&
    normalizeBaseUrl(cachedApiBase) !== normalizeBaseUrl(apiBase)
  ) {
    return null;
  }

  return {
    access,
    refresh:
      payload?.refresh ||
      payload?.refresh_token ||
      payload?.tokens?.refresh ||
      "",
    source: "token_file",
    tokenFile: filePath,
  };
}

export async function writeCachedTokens(
  tokens,
  { apiBase, env = process.env, organizationId = "", workspaceId = "" } = {},
) {
  const filePath = configuredTokenFilePath(env);
  if (!filePath || !tokens?.access) return false;

  const payload = {
    api_base: normalizeBaseUrl(apiBase || ""),
    access: tokens.access,
    refresh: tokens.refresh || "",
    organization_id: organizationId || undefined,
    workspace_id: workspaceId || undefined,
    updated_at: new Date().toISOString(),
  };

  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  await fs.writeFile(tempPath, `${JSON.stringify(payload, null, 2)}\n`, {
    mode: 0o600,
  });
  await fs.rename(tempPath, filePath);
  await fs.chmod(filePath, 0o600).catch(() => null);
  return true;
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}
