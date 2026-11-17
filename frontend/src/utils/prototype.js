// Prototype mode (src/_mock/prototype/README.md): the real app on MSW mocks,
// local only. Components never import src/_mock/prototype; they ask this
// module whether they run in prototype mode, for example to disable an
// outbound link the mocks do not cover (PRODUCT-SPEC §6.1, "Outbound links
// that are not mocked").

// The one tooltip every disabled outbound action shows in prototype mode.
export const NOT_IN_PROTOTYPE = "Not in prototype";

export function prototypeModeFromEnv(env = {}) {
  return env.VITE_ENVIRONMENT === "local" && env.VITE_PROTOTYPE_MODE === "true";
}

// Strict mock mode: unmocked calls answer 599 instead of an empty success.
export function strictUnmockedFromEnv(env = {}) {
  return (
    prototypeModeFromEnv(env) && env.VITE_PROTOTYPE_STRICT_UNMOCKED === "true"
  );
}

const PROTOTYPE_MODE = prototypeModeFromEnv(import.meta.env);

export function isPrototypeMode() {
  return PROTOTYPE_MODE;
}

// Props for an action the prototype does not cover: disabled, with the
// standard tooltip. Outside prototype mode it returns {} so the action works.
// Usage: <CustomTooltip show={...} title={p.title}><span><Button
// disabled={p.disabled} /></span></CustomTooltip>
export function notInPrototypeProps(prototype = PROTOTYPE_MODE) {
  return prototype ? { disabled: true, title: NOT_IN_PROTOTYPE } : {};
}
