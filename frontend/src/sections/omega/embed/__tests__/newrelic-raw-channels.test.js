// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { devTracing, prodTracing } from "src/newrelic";
import {
  setConfiguration,
  getConfigurationValue,
} from "../../../../../node_modules/@newrelic/browser-agent/dist/cjs/common/config/config.js";

// The package does not export its private config subpath. Load the installed
// CommonJS build by file path (same model as the bundled ESM) without starting
// its loader or relying on Node to resolve extensionless private ESM imports.
// Installed 1.264.0 raw HarvestSchedulers bypass the obfuscator. These explicit
// local kills must survive the installed SDK's configuration modeling, rather
// than depending on backend sampling or defaults. No agent or I/O is started.
describe.each([
  ["dev", devTracing],
  ["production", prodTracing],
])("New Relic %s raw harvest channels", (environment, tracing) => {
  it.each(["session_trace", "session_replay", "logging"])(
    "disables %s before the installed scheduler can be started",
    (feature) => {
      const agent = `fake-${environment}-unit-test`;
      setConfiguration(agent, tracing.init);
      expect(tracing.init[feature]?.enabled).toBe(false);
      expect(getConfigurationValue(agent, `${feature}.enabled`)).toBe(false);
    },
  );
});
