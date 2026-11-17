// The session exchange through the REAL host axios instance (src/utils/axios.js),
// its interceptors included, with only the transport adapter replaced. The host
// rejects with `{...response.data, statusCode, transportCode}` and no `response`,
// so a minter that reads `error.response` sees no status at all (verifier
// finding 1 on 1bf593b37): every refusal became "Omega isn't available here".
import { AxiosError, AxiosHeaders } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("src/utils/Mixpanel", () => ({ resetUser: vi.fn() }));

import axiosInstance from "src/utils/axios";

import harnessAxios from "./browser/axios-stub";

import {
  createAssertionMinter,
  SESSION_EXCHANGE_PATH,
} from "../sessionAssertion";
import { createOmegaClient } from "../omegaClient";
import { OMEGA_STATE } from "../omegaStates";
import { errorCopy as sandboxErrorCopy } from "../../sandboxes/components/SandboxErrorState";
import { errorCopy as deploymentErrorCopy } from "../../deployments/components/DeploymentErrorState";

// Django's API envelope (tfc/utils/api_errors.py build_error_envelope), with the
// session exchange's own codes and words (accounts/views/omega_session.py and
// tfc/utils/error_codes.py at omega/p-f-gate 73c9f640a).
const envelope = (type, code, message) => ({
  status: false,
  type,
  code,
  detail: message,
  message,
  error: message,
  result: message,
});
const VIEWER = envelope(
  "permission_error",
  "omega_viewer_not_admitted",
  "Viewers cannot open Omega. Ask a workspace admin for member access.",
);
const NO_ROLE = envelope(
  "permission_error",
  "permission_denied",
  "No compatible Omega Squad role",
);
const FOREIGN = envelope(
  "permission_error",
  "permission_denied",
  "Selected organization or workspace is unavailable",
);
const THROTTLED = envelope(
  "rate_limit_error",
  "throttled",
  "Too many Omega sign-ins in a minute. Wait for the time in Retry-After and try again.",
);
const OFF = envelope(
  "service_unavailable",
  "service_unavailable",
  "Omega delegation is unavailable",
);

/** An adapter that answers like axios' own: `settle` by config.validateStatus. */
const answer =
  (status, data, headers = {}) =>
  async (config) => {
    const response = {
      status,
      statusText: "",
      data,
      headers: new AxiosHeaders({
        "content-type": "application/json",
        ...headers,
      }),
      config,
      request: {},
    };
    if (config.validateStatus?.(status) ?? (status >= 200 && status < 300))
      return response;
    throw new AxiosError(
      `Request failed with status code ${status}`,
      status >= 500 ? AxiosError.ERR_BAD_RESPONSE : AxiosError.ERR_BAD_REQUEST,
      config,
      {},
      response,
    );
  };
const offline = async (config) => {
  throw new AxiosError("Network Error", AxiosError.ERR_NETWORK, config, {});
};

let originalAdapter;
let originalHarnessAdapter;
let seen;
beforeEach(() => {
  originalAdapter = axiosInstance.defaults.adapter;
  originalHarnessAdapter = harnessAxios.defaults.adapter;
  seen = [];
  vi.spyOn(console, "warn").mockImplementation(() => {});
});
afterEach(() => {
  axiosInstance.defaults.adapter = originalAdapter;
  harnessAxios.defaults.adapter = originalHarnessAdapter;
  vi.restoreAllMocks();
});
const use = (adapter) => {
  axiosInstance.defaults.adapter = async (config) => {
    seen.push({ url: config.url, data: config.data });
    return adapter(config);
  };
};
const mintVia = (adapter) => {
  use(adapter);
  return createAssertionMinter()().catch((caught) => caught);
};

describe("the session exchange through the host axios instance", () => {
  it("mints through it: the contracted path and body, the assertion back", async () => {
    const minted = await mintVia(
      answer(200, {
        accessToken: "omega_delegate_h.p.s",
        tokenType: "Bearer",
        expiresIn: 45,
      }),
    );
    expect(minted.token).toBe("omega_delegate_h.p.s");
    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe(SESSION_EXCHANGE_PATH);
    expect(JSON.parse(seen[0].data)).toEqual({ audience: "omega-squad" });
  });

  it("the host's rejection has no `response`, and its `status` is the envelope's flag", async () => {
    use(answer(403, VIEWER));
    const raw = await axiosInstance
      .post(SESSION_EXCHANGE_PATH, { audience: "omega-squad" })
      .catch((caught) => caught);
    expect(raw.response).toBeUndefined();
    expect(raw).toMatchObject({
      status: false,
      statusCode: 403,
      code: "omega_viewer_not_admitted",
    });
  });

  it("a viewer reads the viewer refusal, not 'Omega isn't available here'", async () => {
    const error = await mintVia(answer(403, VIEWER));
    expect(error).toMatchObject({
      name: "OmegaError",
      state: OMEGA_STATE.REFUSED,
      status: 403,
      code: "omega_viewer_not_admitted",
      title: "Viewers can't use Omega",
      message:
        "Viewers can't use Omega in this workspace. Ask a workspace owner or admin for member access.",
      retryAfter: null,
    });
    for (const copyOf of [sandboxErrorCopy, deploymentErrorCopy]) {
      const copy = copyOf(error);
      expect(copy.title).not.toBe(
        copyOf === sandboxErrorCopy
          ? "Omega can't complete this request right now."
          : "Omega isn't responding",
      );
      expect(copy.retry).toBeFalsy();
    }
  });

  it("any other 403 is the exchange's refusal, in Future AGI words", async () => {
    for (const body of [NO_ROLE, FOREIGN]) {
      const error = await mintVia(answer(403, body));
      expect(error).toMatchObject({
        state: OMEGA_STATE.REFUSED,
        status: 403,
        code: "session_exchange_refused",
        title: "Omega isn't available to you here",
      });
      expect(error.message).not.toMatch(/Squad|organization or workspace is/);
    }
  });

  it("a throttled exchange reads the throttle copy; the interceptor drops Retry-After, so 'a few seconds'", async () => {
    const error = await mintVia(
      answer(429, THROTTLED, { "retry-after": "12" }),
    );
    expect(error).toMatchObject({
      state: OMEGA_STATE.UNAVAILABLE,
      status: 429,
      code: "session_exchange_throttled",
      retryAfter: null,
      message:
        "Your account opened Omega too many times in the last minute. Try again in a few seconds.",
    });
    for (const copyOf of [sandboxErrorCopy, deploymentErrorCopy]) {
      expect(copyOf(error)).toMatchObject({
        title:
          copyOf === sandboxErrorCopy
            ? "Omega can't complete this request right now."
            : "Omega isn't responding",
        description: error.message,
        retry: true,
      });
    }
  });

  it("a throttle whose body carries retryAfterSeconds shows that wait", async () => {
    const error = await mintVia(
      answer(429, { ...THROTTLED, retryAfterSeconds: 12 }),
    );
    expect(error).toMatchObject({
      code: "session_exchange_throttled",
      retryAfter: 12,
      message:
        "Your account opened Omega too many times in the last minute. Try again in 12 seconds.",
    });
  });

  it("delegation off and no answer at all are 'not available', with no Django words", async () => {
    for (const adapter of [answer(503, OFF), answer(500, {}), offline]) {
      const error = await mintVia(adapter);
      expect(error).toMatchObject({
        state: OMEGA_STATE.UNAVAILABLE,
        code: "session_exchange_unavailable",
        title: "Omega isn't available here",
      });
      expect(error.message).not.toMatch(/delegation/i);
    }
  });

  it("omegaFetch returns the same refusals and never calls the gateway", async () => {
    const fetchImpl = vi.fn();
    const client = createOmegaClient({
      mintAssertion: createAssertionMinter(),
      fetchImpl,
    });
    const cases = [
      [answer(403, VIEWER), OMEGA_STATE.REFUSED, "omega_viewer_not_admitted"],
      [answer(403, NO_ROLE), OMEGA_STATE.REFUSED, "session_exchange_refused"],
      [
        answer(429, THROTTLED),
        OMEGA_STATE.UNAVAILABLE,
        "session_exchange_throttled",
      ],
      [
        answer(503, OFF),
        OMEGA_STATE.UNAVAILABLE,
        "session_exchange_unavailable",
      ],
    ];
    for (const [adapter, state, code] of cases) {
      use(adapter);
      const result = await client.omegaFetch("/workspace/sandboxes");
      expect(result).toMatchObject({ ok: false, state, code });
      expect(result.title).toBeTruthy();
    }
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("the browser harness's axios stub matches the host instance (contract)", () => {
  const ANSWERS = {
    viewer: answer(403, VIEWER),
    refused: answer(403, NO_ROLE),
    throttled: answer(429, THROTTLED, { "retry-after": "30" }),
    down: answer(503, OFF),
    offline,
  };
  const fields = (error) => ({
    name: error.name,
    state: error.state,
    status: error.status,
    code: error.code,
    title: error.title,
    message: error.message,
    retryAfter: error.retryAfter,
  });

  it("rejects with the same object, and the minter reads the same refusal", async () => {
    for (const [name, adapter] of Object.entries(ANSWERS)) {
      axiosInstance.defaults.adapter = adapter;
      harnessAxios.defaults.adapter = adapter;
      const post = (instance) =>
        instance
          .post(SESSION_EXCHANGE_PATH, { audience: "omega-squad" })
          .catch((caught) => caught);
      const [host, stub] = [
        await post(axiosInstance),
        await post(harnessAxios),
      ];
      expect(stub, name).toEqual(host);
      expect(stub.response, name).toBeUndefined();
      const viaHost = await createAssertionMinter()().catch((e) => e);
      const viaStub = await createAssertionMinter({
        post: (url, body) => harnessAxios.post(url, body),
      })().catch((e) => e);
      expect(fields(viaStub), name).toEqual(fields(viaHost));
    }
  });
});
