"use strict";
const path = require("node:path");
const fs = require("node:fs");
const { catalogue } = require("./catalogue.cjs");
const { browserProfile, fragmentProfile } = require("./browser-profile.cjs");
const CONFIG = "journeys/gateway/playwright.journeys.config.ts";
const REPORTER = "./journeys/gateway/_invocation/reporter.cjs";
const ABI_REQUEST_SHA256 =
  "b78a171fea942f88d1900ae4e3388e40a11593397ca733441abe4759daf6e803";

// Proposed consumer binding, NOT a claim that the still-owed CONSUMER-ABI.json
// accepts these exports. E0 records below use exactly the requested core keys.
// The accepted compiler must consume sidecars; ignoring them must STOP admission.
const fixtureOrder = Object.freeze([
  "accepted SourceInputs.check at exact sourceRef; lockfiles before PREPARE",
  "accepted phase-0/step-1 two-list guard, native identity and image/read-closure census",
  "consumer appSide build/runtime profiles; beforeFrontendBuild scratch seal before context consumption",
  "accepted guards before worker-environment observer in launcher and EVERY worker",
  "create-only per-invocation binding and reporter outputs, retained base reporters",
  "same-job appSide beforeWalk and context observer before ANY browser page",
  "guard fixture-owned loopback listeners/clients as own-host; never external provider egress",
  "synthetic IDs; fixture browser/anonymous stock page only with explicit admitted viewport",
  "native starts/steps/terminals plus attachment reconciliation; unmodified originals",
]);
const escapeRegex = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const titleGrep = (title) => `(?:^| )${escapeRegex(title)}(?:$| @)`;
function outputPath(value) {
  if (
    typeof value !== "string" ||
    !path.isAbsolute(value) ||
    path.resolve(value) !== value ||
    value === "/"
  ) {
    throw new Error(
      "binding output root must be a canonical absolute landing directory",
    );
  }
  const repo = path.resolve(__dirname, "../../../..");
  if (value === repo || value.startsWith(`${repo}${path.sep}`))
    throw new Error("binding output cannot be in source");
  return value;
}
function browsers(value) {
  if (
    !Array.isArray(value) ||
    JSON.stringify(value) !== JSON.stringify(["chromium", "firefox"])
  ) {
    throw new Error(
      "browser inventory must be exactly chromium then firefox; WebKit remains owed",
    );
  }
}
function common(input) {
  if (!/^[a-f0-9]{40}$/.test(input.sourceRef))
    throw new Error("binding requires exact sourceRef");
  outputPath(input.outputRoot);
  browsers(input.browsers);
}
function bindingFor(input, browser, item, mode) {
  const index = item
    ? String(catalogue.indexOf(item) + 1).padStart(3, "0")
    : "all";
  const fragment =
    item?.kind === "product-fragment"
      ? fragmentProfile(input.stockFragment)
      : null;
  const context =
    fragment ||
    (item?.browserContextUsed ? browserProfile(input.fixtureBrowser) : null);
  const id = `gwj-${index}-${browser}${context ? `-w${context.viewport.width}-h${context.viewport.height}` : ""}-${mode}`;
  return {
    schema: "gateway-journey-invocation/v1",
    sourceRef: input.sourceRef,
    id,
    mode,
    browser,
    expectedIds: item ? [item.id] : catalogue.map((row) => row.id),
    network: item?.network || "none",
    profile: fragment ? "stock" : "none",
    stack: fragment ? "standalone" : "none",
    viewport: context?.viewport || null,
    widthAuthority:
      context?.widthAuthority || "NOT_APPLICABLE_NO_BROWSER_CONTEXT",
    // Dispatchable entries use synthetic data, local fixtures or anonymous HTTP.
    // Runtime rows remain OWED; no product/tenant binding is fabricated here.
    tenants: [],
    appUrl: fragment?.appUrl || "",
    gatewayUrl: fragment?.gatewayUrl || "",
    outputRoot: path.join(input.outputRoot, id),
    productAccepted: false,
    fullRows: [],
  };
}
function plan(binding) {
  validateBinding(binding);
  const item =
    binding.mode === "execute"
      ? catalogue.find((x) => x.id === binding.expectedIds[0])
      : null;
  const argv = [
    "yarn",
    "playwright",
    "test",
    `--config=${CONFIG}`,
    "--workers=1",
    "--retries=0",
    "--repeat-each=1",
    `--reporter=line,${REPORTER}`,
  ];
  if (item)
    argv.push(`${escapeRegex(item.file)}$`, `--grep=${titleGrep(item.title)}`);
  else argv.push("--list");
  const bindingFile = path.join(binding.outputRoot, "invocation.json");
  return {
    consumerInvocation: {
      id: binding.id,
      type: "browser",
      argv,
      cwd: "e2e",
      network: binding.network,
      browser: binding.browser,
    },
    bindingFile,
    binding,
    environment: {
      GW_JOURNEY_BINDING: bindingFile,
      ...(item?.kind === "product-fragment"
        ? {
            E2E_STACK: "standalone",
            E2E_GATEWAY_PROFILE: "stock",
            E2E_APP_URL: binding.appUrl,
            E2E_GATEWAY_URL: binding.gatewayUrl,
          }
        : {}),
    },
    reporter: {
      module: REPORTER,
      options: {},
      outputFile: path.join(binding.outputRoot, "reconciliation.json"),
    },
    fixtureOrder,
  };
}
function discoveryInvocations(input) {
  common(input);
  return input.browsers.map((browser) =>
    plan(bindingFor(input, browser, null, "discover")),
  );
}
function invocationPlans(input) {
  common(input);
  // Discovery ALWAYS inventories DARK, owed rows and browser fixtures too.
  // Defaults execute only source-only detectors/non-browser fixtures. Browser
  // fixtures and the stock fragment need explicit selection + admitted viewport.
  let selected = catalogue.filter(
    (x) =>
      x.disposition === "SOURCE_ONLY" &&
      !x.browserContextUsed &&
      ["detector-only", "fixture-only"].includes(x.kind),
  );
  if (input.includeIds !== undefined) {
    if (
      !Array.isArray(input.includeIds) ||
      !input.includeIds.length ||
      new Set(input.includeIds).size !== input.includeIds.length
    )
      throw new Error("invalid invocation inventory");
    selected = input.includeIds.map((id) => {
      const item = catalogue.find((x) => x.id === id);
      if (!item) throw new Error("unknown invocation inventory identity");
      if (item.disposition === "DARK")
        throw new Error("DARK max_charge inventory is not dispatchable");
      if (item.disposition !== "SOURCE_ONLY")
        throw new Error(
          "OWED NOT_WALKED guard/runtime row is not executable coverage",
        );
      return item;
    });
    // Do not let callers reorder restored before red (or vary source ordering).
    selected.sort((a, b) => catalogue.indexOf(a) - catalogue.indexOf(b));
  }
  return input.browsers.flatMap((browser) =>
    selected.map((item) => plan(bindingFor(input, browser, item, "execute"))),
  );
}
function validateBinding(binding) {
  if (
    !binding ||
    binding.schema !== "gateway-journey-invocation/v1" ||
    !/^[a-f0-9]{40}$/.test(binding.sourceRef) ||
    !["discover", "execute"].includes(binding.mode) ||
    !["chromium", "firefox"].includes(binding.browser) ||
    !/^gwj-(?:all|[0-9]{3})-(?:chromium|firefox)(?:-w[0-9]+-h[0-9]+)?-(?:discover|execute)$/.test(
      binding.id,
    ) ||
    binding.productAccepted !== false ||
    !Array.isArray(binding.fullRows) ||
    binding.fullRows.length !== 0 ||
    !Array.isArray(binding.tenants) ||
    binding.tenants.length !== 0
  )
    throw new Error("invalid invocation binding");
  outputPath(binding.outputRoot);
  if (path.basename(binding.outputRoot) !== binding.id)
    throw new Error("binding output identity mismatch");
  const ids = binding.expectedIds;
  if (!Array.isArray(ids) || !ids.length || new Set(ids).size !== ids.length)
    throw new Error("binding inventory empty/duplicate");
  if (binding.mode === "discover") {
    if (JSON.stringify(ids) !== JSON.stringify(catalogue.map((x) => x.id)))
      throw new Error("binding discovery inventory drift");
  } else if (
    ids.length !== 1 ||
    !catalogue.some((x) => x.id === ids[0] && x.disposition === "SOURCE_ONLY")
  ) {
    throw new Error(
      "binding execution inventory is DARK, OWED, unknown or not singular",
    );
  }
  const item =
    binding.mode === "execute" ? catalogue.find((x) => x.id === ids[0]) : null;
  const fragment = item?.kind === "product-fragment";
  if (fragment) {
    fragmentProfile({
      ...binding.viewport,
      widthAuthority: binding.widthAuthority,
      appUrl: binding.appUrl,
      gatewayUrl: binding.gatewayUrl,
    });
  } else if (item?.browserContextUsed) {
    browserProfile({
      ...binding.viewport,
      widthAuthority: binding.widthAuthority,
    });
    if (binding.appUrl !== "" || binding.gatewayUrl !== "")
      throw new Error("fixture browser cannot bind product endpoints");
  } else if (
    binding.viewport !== null ||
    binding.appUrl !== "" ||
    binding.gatewayUrl !== "" ||
    binding.widthAuthority !== "NOT_APPLICABLE_NO_BROWSER_CONTEXT"
  )
    throw new Error("binding invents browser coverage");
  if (
    binding.profile !== (fragment ? "stock" : "none") ||
    binding.stack !== (fragment ? "standalone" : "none") ||
    binding.network !== (item?.network || "none")
  )
    throw new Error("binding profile/network mismatch");
  const index = item
    ? String(catalogue.indexOf(item) + 1).padStart(3, "0")
    : "all";
  const expectedId = `gwj-${index}-${binding.browser}${item?.browserContextUsed ? `-w${binding.viewport.width}-h${binding.viewport.height}` : ""}-${binding.mode}`;
  if (binding.id !== expectedId)
    throw new Error("binding selected identity drift");
  return binding;
}
function validateArguments(binding, argv) {
  const expected = plan(binding).consumerInvocation.argv.slice(2);
  const start = argv.indexOf("test", 2);
  const actual = start < 0 ? [] : argv.slice(start);
  if (
    actual.length !== expected.length ||
    actual.some((arg, index) => {
      const original = expected[index];
      if (!original.startsWith("--reporter=")) return arg !== original;
      if (!arg.startsWith(original)) return true;
      const reporters = arg.slice("--reporter=".length).split(",");
      return (
        reporters.length < 2 ||
        new Set(reporters).size !== reporters.length ||
        reporters.some((value) => !value) ||
        reporters[0] !== "line" ||
        reporters[1] !== REPORTER
      );
    })
  )
    throw new Error(
      "binding argv drift; only additional accepted reporters may be appended",
    );
}
function readBinding() {
  const filename = process.env.GW_JOURNEY_BINDING;
  if (!filename || !path.isAbsolute(filename))
    throw new Error(
      "runner must bind GW_JOURNEY_BINDING before discovery/execution",
    );
  const binding = validateBinding(
    JSON.parse(fs.readFileSync(filename, "utf8")),
  );
  if (
    filename !== path.join(binding.outputRoot, "invocation.json") ||
    fs.realpathSync(filename) !== filename
  ) {
    throw new Error(
      "binding file must be the create-only canonical invocation landing file",
    );
  }
  return binding;
}
function orderedInvocations(input) {
  // E0 ABI core: exact id/type/argv/cwd/network/browser; no extra ignored fields.
  // Both full discovery inventories MUST pass before the first execute record.
  return [...discoveryInvocations(input), ...invocationPlans(input)].map(
    (p) => p.consumerInvocation,
  );
}
function consumerExtension(input) {
  const plans = [...discoveryInvocations(input), ...invocationPlans(input)];
  return {
    schema: "gateway-journeys-consumer-request/v1",
    sourceRef: input.sourceRef,
    abiRequestSha256: ABI_REQUEST_SHA256,
    lockfiles: [
      { manager: "yarn-classic", cwd: "frontend", path: "frontend/yarn.lock" },
      { manager: "yarn-classic", cwd: "e2e", path: "e2e/yarn.lock" },
    ],
    orderedInvocations: plans.map((p) => p.consumerInvocation),
    // Runner must install these before config loading, not ignore them. Values
    // contain nonsecret selection metadata only; no actor/session objects.
    invocationBindings: plans.map(({ consumerInvocation, ...sidecar }) => ({
      id: consumerInvocation.id,
      ...sidecar,
    })),
    fixtureOrder,
    productAccepted: false,
    runtimeQualified: false,
  };
}
module.exports = {
  ABI_REQUEST_SHA256,
  CONFIG,
  REPORTER,
  fixtureOrder,
  titleGrep,
  orderedInvocations,
  invocationPlans,
  discoveryInvocations,
  consumerExtension,
  validateBinding,
  validateArguments,
  readBinding,
};
