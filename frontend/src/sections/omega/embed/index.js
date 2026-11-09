// Public surface of the embed host. Tab lanes (Studio, Console) import from
// here, never from the files inside.
export { default as EmbeddedSurface } from "./EmbeddedSurface";
export { FRAME_ALLOW, FRAME_CLASS } from "./frame-attributes";
export { EMBED_STATUS, NO_KEY_GATE_TIMEOUT_MS } from "./use-embed-session";
export {
  DIAGNOSIS,
  HELLO_TIMEOUT_MS,
  diagnoseMissingHello,
  diagnosisCopy,
} from "./diagnosis";
export {
  HANDOFF_TTL_MS,
  clearOmegaConnect,
  hasOmegaConnectToken,
  offerOmegaConnect,
  omegaConnectGeneration,
  onOmegaConnect,
  peekOmegaConnect,
  takeOmegaConnectToken,
} from "./connect-intake";
export { EMBED_MESSAGE, EMBED_PROTOCOL, normalizeEmbedPath } from "./protocol";
export { isEmbedFramePath } from "./telemetry/guard";
