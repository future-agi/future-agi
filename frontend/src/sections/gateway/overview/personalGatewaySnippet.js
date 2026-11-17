// Only a freshly issued key held by the current surface replaces environment placeholders.
export function fillGatewaySnippet(code, personalKey) {
  if (!/^[a-zA-Z0-9_-]+$/.test(personalKey || "")) return code;
  return code
    .replace(/\$AGENTCC_API_KEY/g, () => personalKey)
    .replace(
      /os\.environ\["AGENTCC_API_KEY"\]|process\.env\.AGENTCC_API_KEY/g,
      () => JSON.stringify(personalKey),
    );
}
