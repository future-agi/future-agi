import * as z from "zod/v4";

const environmentSchema = z.object({
  MCP_PORT: z.coerce.number().int().min(1).max(65_535).default(3001),
  MCP_API_BASE_URL: z.url().default("http://backend"),
  MCP_API_TIMEOUT_MS: z.coerce.number().int().min(100).max(120_000).default(30_000),
  MCP_MAX_RESPONSE_BYTES: z.coerce
    .number()
    .int()
    .min(1_024)
    .max(50_000_000)
    .default(250_000),
  MCP_ALLOWED_HOSTS: z.string().default(""),
});

export interface AppConfig {
  port: number;
  apiBaseUrl: string;
  apiTimeoutMs: number;
  maxResponseBytes: number;
  allowedHosts: ReadonlySet<string>;
}

export function loadConfig(environment: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = environmentSchema.parse(environment);
  return {
    port: parsed.MCP_PORT,
    apiBaseUrl: parsed.MCP_API_BASE_URL.replace(/\/$/u, ""),
    apiTimeoutMs: parsed.MCP_API_TIMEOUT_MS,
    maxResponseBytes: parsed.MCP_MAX_RESPONSE_BYTES,
    allowedHosts: new Set(
      parsed.MCP_ALLOWED_HOSTS.split(",")
        .map((host) => host.trim().toLowerCase())
        .filter(Boolean),
    ),
  };
}
