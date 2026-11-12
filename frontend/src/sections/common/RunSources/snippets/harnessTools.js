// Select explicitly disabled tools supported by the target configuration.
export function excludedToolsOf(server, mcpMode) {
  if (mcpMode !== "gemini" && mcpMode !== "codex") return [];
  const names = server?.tool_names ?? [];
  const off = new Set(server?.disabled_tools ?? []);
  return names.filter((t) => off.has(t));
}
