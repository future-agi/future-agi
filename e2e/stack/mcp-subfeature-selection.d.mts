export interface McpSubfeatureDeclaration {
  selection: string | undefined;
  profile: string;
  gates: Record<string, string>;
  mockAuth: string;
}
export const MCP_GATE_KEYS: readonly string[];
export function subfeatureDeclaration(env?: Record<string, string | undefined>): McpSubfeatureDeclaration;
export function mcpSubfeatureExclusionPattern(env?: Record<string, string | undefined>): RegExp;
export function validateMcpGateEnvironment(service: string, env: Record<string, string>, declaration: McpSubfeatureDeclaration, complete?: boolean): void;
export function validateMcpGateEntries(service: string, entries: string[], declaration: McpSubfeatureDeclaration): void;
