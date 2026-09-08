import type * as z from "zod/v4";

export type ToolRisk = "read" | "write" | "destructive";
export type ParameterLocation = "path" | "query" | "body";

export interface GeneratedTool {
  name: string;
  operationId: string;
  enabled: boolean;
  group: string;
  title: string;
  description: string;
  risk: ToolRisk;
  method: string;
  path: string;
  parameterLocations: Record<string, ParameterLocation>;
  inputJsonSchema: Record<string, unknown>;
}

export interface ApiCredentials {
  apiKey?: string;
  secretKey?: string;
  bearerToken?: string;
}

export type ToolSchemaMap = Record<
  string,
  z.ZodObject<z.ZodRawShape>
>;
