/* eslint-disable react/prop-types */
import React from "react";
import { afterAll, vi } from "vitest";

// Install before application imports. The parent runner must ALSO supply its
// established process-level no-egress guard; this is not a socket sandbox.
const offline = await vi.hoisted(async () => {
  const deny = () => {
    throw new Error("Unexpected network access in rerun admission test");
  };
  const globals = ["fetch", "XMLHttpRequest", "WebSocket", "EventSource"];
  const saved = globals.map((key) => [
    key,
    Object.getOwnPropertyDescriptor(globalThis, key),
  ]);
  globals.forEach((key) =>
    Object.defineProperty(globalThis, key, {
      configurable: true,
      writable: true,
      value: deny,
    }),
  );
  const beacon = Object.getOwnPropertyDescriptor(navigator, "sendBeacon");
  Object.defineProperty(navigator, "sendBeacon", {
    configurable: true,
    value: deny,
  });
  const { default: axios } = await import("axios");
  const adapter = axios.defaults.adapter;
  axios.defaults.adapter = deny;
  return { axios, adapter, saved, beacon };
});
afterAll(() => {
  offline.axios.defaults.adapter = offline.adapter;
  offline.saved.forEach(([key, descriptor]) => {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else delete globalThis[key];
  });
  if (offline.beacon)
    Object.defineProperty(navigator, "sendBeacon", offline.beacon);
  else delete navigator.sendBeacon;
});

export const WARNING =
  "Re-run admission was not confirmed for every experiment. Check execution status before retrying.";
export const GENERIC = "Something went wrong";
export const GATEWAY =
  "Gateway authentication failed. Ask your administrator to check the gateway credentials.";
const TEXT_KEYS = ["detail", "message", "error", "result"];
// GeneralMethods.internal_server_error_response -> build_error_envelope.
export const envelope = (message = WARNING, code = "server_error") => ({
  status: false,
  type: "server_error",
  code,
  detail: message,
  message,
  error: message,
  result: message,
});
export const fallbacks = [
  ["other 5xx", 503, envelope()],
  ["other status", 403, envelope()],
  ["other code", 500, envelope(WARNING, "internal_error")],
  ["missing code", 500, { ...envelope(), code: undefined }],
  ["newline", 500, envelope(`${WARNING}\n`)],
  ["carriage return", 500, envelope(`${WARNING}\r`)],
  ["prefix", 500, envelope(`Failure: ${WARNING}`)],
  ["whitespace", 500, envelope(` ${WARNING}`)],
  [
    "infrastructure body",
    500,
    envelope("Traceback DB:: SELECT sensitive_column FROM private_table"),
  ],
  ["array result", 500, { ...envelope(), result: [WARNING] }],
  ["nested envelope", 500, { result: envelope() }],
  ["wrong envelope status", 500, { ...envelope(), status: true }],
  ["wrong envelope type", 500, { ...envelope(), type: "service_unavailable" }],
  ...TEXT_KEYS.map((key) => [
    `different ${key}`,
    500,
    { ...envelope(), [key]: "Traceback private upstream detail" },
  ]),
  ...TEXT_KEYS.map((key) => [
    `missing ${key}`,
    500,
    Object.fromEntries(
      Object.entries(envelope()).filter(([name]) => name !== key),
    ),
  ]),
];

const ui = vi.hoisted(() => ({
  id: "22222222-2222-4222-8222-222222222222",
  secondId: "33333333-3333-4333-8333-333333333333",
  snackbar: vi.fn(),
  refresh: vi.fn(),
  clearSelection: vi.fn(),
}));
export { ui };

vi.mock("src/config-global", () => ({ HOST_API: "https://offline.invalid" }));
vi.mock("notistack", () => ({ enqueueSnackbar: ui.snackbar }));
vi.mock("src/utils/Mixpanel", () => ({
  trackEvent: vi.fn(),
  resetUser: vi.fn(),
  Events: {},
  PropertyName: {},
}));
vi.mock("src/utils/logger", () => ({
  default: { debug: vi.fn(), warn: vi.fn(), error: vi.fn() },
}));
vi.mock("src/utils/recaptchaService", () => ({
  getRecaptchaToken: vi.fn(async () => "synthetic-recaptcha"),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
vi.mock("react-router", () => ({
  useNavigate: () => vi.fn(),
  useParams: () => ({ experimentId: ui.id }),
}));

// Only visual leaves and unrelated edit/picker doors are inert. Header/Row,
// common.js's API re-export, useReRunExperiment, Axios and Query stay real.
vi.mock("@mui/material", () => ({
  Box: ({ children }) => <div>{children}</div>,
  Typography: ({ children }) => <span>{children}</span>,
  Button: ({ children, onClick, disabled }) => (
    <button onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  useTheme: () => ({ spacing: (n) => n * 8, palette: { text: {} } }),
}));
vi.mock("@mui/lab", () => ({
  LoadingButton: ({ children, onClick, loading }) => (
    <button onClick={onClick} disabled={loading}>
      {children}
    </button>
  ),
}));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/components/FormSearchField/FormSearchField", () => ({
  default: () => null,
}));
vi.mock("src/components/show", () => ({
  ShowComponent: ({ condition, children }) => (condition ? children : null),
}));
vi.mock("../../Common/CustomDialog/CustomDialog", () => ({
  default: ({ open, title, actionButton, onClickAction, children }) =>
    open ? (
      <div role="dialog" aria-label={title}>
        {children}
        <button onClick={onClickAction}>{actionButton}</button>
      </div>
    ) : null,
}));
vi.mock("../../Common/BackButton", () => ({ default: () => null }));
vi.mock("../../Experiment/constants", () => ({}));
vi.mock("../../Experiment/schema", () => ({}));
vi.mock("../../Experiment/utils", () => ({}));
vi.mock(
  "src/sections/experiment-detail-classic/DevelopExperimentSelect",
  () => ({
    default: () => null,
  }),
);
vi.mock("src/sections/experiment-detail-classic/ClassicEditExperiment", () => ({
  default: () => null,
}));
vi.mock("src/sections/experiment-detail-classic/ClassicRerunAction", () => ({
  default: () => null,
}));
vi.mock("src/sections/experiment-detail-classic/experiment-context", () => ({
  useExperimentDetailContext: () => ({
    experimentGridRef: { current: { api: { refreshServerSide: ui.refresh } } },
  }),
}));
