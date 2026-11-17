/* eslint-env node */
// Source-copy pins run without importing Django or opening a database.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const source = (file) =>
  readFileSync(
    resolve(process.cwd(), "../futureagi/simulate/views", file),
    "utf8",
  );

describe("CBSEC approved response copy", () => {
  it("S2 pins both failed-start response literals", () => {
    const text = source("run_test.py");
    const literal =
      '"Failed to start test execution. Check the test runs before trying again."';
    expect(text.split(literal)).toHaveLength(3);
    expect(text).not.toContain('"Failed to start test execution"');
  });

  it("S4 pins the outer response without changing the private log", () => {
    const text = source("run_test.py");
    expect(text).toMatch(
      /internal_server_error_response\(\s*"Failed to execute test\. Check the test runs before trying again\."\s*\)/,
    );
    expect(text).toContain('logger.exception("Failed to execute test")');
  });

  it("S5 pins uncertainty-aware creation guidance without changing the private log", () => {
    const text = source("agent_definition.py");
    expect(text).toMatch(
      /internal_server_error_response\(\s*"Agent definition creation could not be confirmed\. Check the agent definitions before trying again\."\s*\)/,
    );
    expect(text).toContain(
      'logger.exception("Failed to create agent definition")',
    );
  });

  it("S3 keeps both approved service-unavailable literals unchanged", () => {
    expect(
      source("run_test.py").split(
        '"Test execution service is unavailable. Please try again later."',
      ),
    ).toHaveLength(3);
  });
});
