import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import * as projection from "../saml-telemetry-urls";

const main = readFileSync("src/main.jsx", "utf8");
const start = main.indexOf("Sentry.init({");
const end = main.indexOf("\n});", start) + "\n});".length;
const marked = "https://app.invalid/dashboard?keep=1&saml_code=example";
const clean = "https://app.invalid/dashboard?keep=1";

// Execute the shipped initialization, replacing only the SDK boundary. This
// checks that each configured callback actually uses the shared projection.
function sentryOptions() {
  let options;
  // Replace only the build-time release value for this browserless execution.
  const source = main
    .slice(start, end)
    .replace("import.meta.env.VITE_APP_VERSION", "undefined");
  runInNewContext(source, {
    ...projection,
    Sentry: {
      init(value) {
        options = value;
      },
      browserTracingIntegration: (value) => ({ name: "Tracing", ...value }),
    },
    SENTRY_DSN: "",
    CURRENT_ENVIRONMENT: "local",
    HOST_API: "https://api.invalid",
    sentryTracesSampleRate: 0.1,
    SENTRY_IGNORE_ERRORS: [],
  });
  return options;
}

describe("Sentry callback URL configuration", () => {
  it("projects navigation breadcrumbs before buffering", () => {
    const options = sentryOptions();
    expect(
      options.beforeBreadcrumb({ data: { from: marked, to: "/ordinary" } }),
    ).toEqual({ data: { from: clean, to: "/ordinary" } });
  });

  it("projects error URLs while retaining ordinary diagnostics", () => {
    const options = sentryOptions();
    expect(
      options.beforeSend({
        message: marked,
        request: { url: marked },
        extra: { counter: 2 },
      }),
    ).toEqual({
      message: clean,
      request: { url: clean },
      extra: { counter: 2 },
    });
  });

  it.each([
    "/node_modules/.vite/deps/chunk.js",
    "/assets/recording-registry.js",
    "/assets/session-recording.js",
    "/assets/rrweb-compiled.js",
  ])("retains the existing error filter for %s", (filename) => {
    expect(
      sentryOptions().beforeSend({
        exception: { values: [{ stacktrace: { frames: [{ filename }] } }] },
      }),
    ).toBeNull();
  });

  it("projects transaction URLs", () => {
    expect(
      sentryOptions().beforeSendTransaction({
        type: "transaction",
        transaction: marked,
        spans: [{ description: marked }],
      }),
    ).toEqual({
      type: "transaction",
      transaction: clean,
      spans: [{ description: clean }],
    });
  });

  it("projects standalone span URLs", () => {
    expect(
      sentryOptions().beforeSendSpan({
        description: marked,
        attributes: { "url.full": marked, duration: 2 },
      }),
    ).toEqual({
      description: clean,
      attributes: { "url.full": clean, duration: 2 },
    });
  });

  it("leaves Replay registration to the post-paint effect", () => {
    expect(
      sentryOptions().integrations.find(
        (integration) => integration.name === "Replay",
      ),
    ).toBeUndefined();
  });

  it("projects Replay metadata through the configured event processor", () => {
    const integration = sentryOptions().integrations.find(
      (item) => item.name === "SamlUrlPrivacy",
    );
    expect(
      integration.processEvent({ type: "replay_event", urls: [marked] }),
    ).toEqual({ type: "replay_event", urls: [clean] });
  });

  it("projects sampling headers through the configured transport hook", () => {
    const integration = sentryOptions().integrations.find(
      (item) => item.name === "SamlUrlPrivacy",
    );
    let hook;
    integration.setup({
      on(name, callback) {
        expect(name).toBe("beforeEnvelope");
        hook = callback;
      },
    });
    const envelope = [{ trace: { transaction: marked, sampled: "true" } }, []];
    hook(envelope);
    expect(envelope[0]).toEqual({
      trace: { transaction: clean, sampled: "true" },
    });
  });
});
