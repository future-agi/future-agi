// Published contract at 18519f182cee7c61ef70b959d920bd710e0a6841:
// docs/gateway/release-notes.md C72 (playground relay outcome), N11
// (warn/log headers), and agentcc-gateway/README.md response-header table.
// Only a playground relay envelope may supply `blocked`; never promote
// body.error.type, body.blocked or direct Axios data into that field.
// Blocks are HTTP 403 + the relay's strict boolean. N11 does not publish a
// block-attestation header: warn/log headers are NOT block evidence.
export function isRelayGuardrailBlock(result) {
  const status = Number(result?.status_code ?? result?.statusCode);
  return status === 403 && result?.blocked === true;
}
