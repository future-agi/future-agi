import fs from "node:fs";
import path from "node:path";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as surface from "../api-surface";
import {
  API_CONTRACT_EXCEPTIONS,
  API_CONTRACT_EXCEPTION_STATUSES,
} from "../api-contract-exceptions";
import { API_SURFACE_PATHS } from "../api-surface.generated";
import {
  ROW_READ_TEMPLATE,
  rowReadRequest,
  rowApiCopyProps,
} from "src/sections/develop-detail/DataTab/DatapointDrawerV2/rowApiPath";
import { setLaunchOverrideForTest } from "src/launch/launchSwitch";

const traverse = traverseModule.default ?? traverseModule;
const ROW_SOURCE =
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/rowApiPath.js";
const REASON =
  "gated by DSX_DATASET_ROW_SOURCE_ENABLED, off by default; not in the public surface while off";
const source = () => fs.readFileSync(path.resolve(ROW_SOURCE), "utf8");

afterEach(() => {
  vi.restoreAllMocks();
  setLaunchOverrideForTest(null);
});

describe("the default-off dataset row read exception", () => {
  it("registers exactly the row read with complete metadata and a removal step", () => {
    const entries = Object.entries(API_CONTRACT_EXCEPTIONS).filter(
      ([, meta]) => meta.reason === REASON,
    );
    expect(entries).toEqual([
      [
        ROW_READ_TEMPLATE,
        {
          group: "model-hub",
          status: API_CONTRACT_EXCEPTION_STATUSES.ACTIVE_UNCONTRACTED,
          reason: REASON,
          next: expect.stringMatching(
            /DSX_DATASET_ROW_SOURCE_ENABLED.*ON.*regenerate.*route.*apiPath\(\).*delete/i,
          ),
          methods: ["get"],
        },
      ],
    ]);
    expect(surface.isContractedApiPath(ROW_READ_TEMPLATE)).toBe(false);
  });

  it("formats through the exception helper when a GET is available", () => {
    // Simulate a future ON surface without editing generated artifacts.
    vi.spyOn(surface, "getContractedApiMethods").mockReturnValue(["get"]);
    const format = vi.spyOn(surface, "uncontractedApiPath");
    const contractedFormat = vi.spyOn(surface, "apiPath");
    expect(rowReadRequest("dataset one", "row/one")).toEqual({
      method: "GET",
      path: "/model-hub/datasets/dataset%20one/rows/row%2Fone/",
    });
    expect(format).toHaveBeenCalledTimes(1);
    expect(format).toHaveBeenCalledWith(ROW_READ_TEMPLATE, {
      dataset_id: "dataset one",
      row_id: "row/one",
    });
    expect(contractedFormat).not.toHaveBeenCalled();
    expect(rowReadRequest(null, "row/one")).toBeNull();
    expect(rowReadRequest("dataset one", "")).toBeNull();
  });

  it("does not treat the exception as evidence that the server GET is enabled", () => {
    expect(surface.getContractedApiMethods(ROW_READ_TEMPLATE)).toEqual([]);
    expect(rowReadRequest("dataset", "row")).toBeNull();
    expect(rowApiCopyProps("dataset", "row", true)).toEqual({});
    setLaunchOverrideForTest({ launch: false });
    expect(rowApiCopyProps("dataset", "row")).toEqual({});
  });

  it("keeps the launch default hidden even if a GET is available", () => {
    vi.spyOn(surface, "getContractedApiMethods").mockReturnValue(["get"]);
    setLaunchOverrideForTest({ launch: true, experimentsBasic: false });
    expect(rowApiCopyProps("dataset", "row")).toEqual({});
  });

  it("refuses a sibling rather than widening the exception", () => {
    const sibling = "/model-hub/datasets/{dataset_id}/rows/";
    expect(surface.isApiContractExceptionPath(sibling)).toBe(false);
    expect(() =>
      surface.uncontractedApiPath(sibling, { dataset_id: "dataset" }),
    ).toThrow("API contract exception path is not registered");
    expect(() => surface.uncontractedApiPath(ROW_READ_TEMPLATE)).toThrow(
      'Missing uncontracted API path param "dataset_id"',
    );
  });

  it("has no static apiPath call outside the generated surface", () => {
    const violations = [];
    traverse(parse(source(), { sourceType: "module" }), {
      CallExpression(nodePath) {
        const { callee, arguments: args } = nodePath.node;
        if (callee.type !== "Identifier" || callee.name !== "apiPath") return;
        const template =
          args[0]?.type === "StringLiteral" ? args[0].value : null;
        if (!template || !Object.hasOwn(API_SURFACE_PATHS, template)) {
          violations.push(template ?? "<dynamic>");
        }
      },
    });
    expect(violations).toEqual([]);
  });

  it("counts the live copy-helper call, not comments or an unused registration", async () => {
    const { collectContractExceptionCalls } = await import(
      "../../../../scripts/collect-api-contract-exceptions.mjs"
    );
    const calls = collectContractExceptionCalls(source());
    expect(calls).toEqual([
      {
        value: ROW_READ_TEMPLATE,
        inlineReason: false,
        line: expect.any(Number),
      },
    ]);
    expect(
      collectContractExceptionCalls(
        `// uncontractedApiPath("${ROW_READ_TEMPLATE}")`,
      ),
    ).toEqual([]);
    expect(
      collectContractExceptionCalls(`const unused = "${ROW_READ_TEMPLATE}";`),
    ).toEqual([]);
    const checker = fs.readFileSync(
      path.resolve("scripts/check-api-endpoint-registry-contract.mjs"),
      "utf8",
    );
    expect(checker).toContain(ROW_SOURCE);
    expect(checker).toMatch(
      /usedContractExceptionSurfacePaths[\s\S]*copyContractExceptionPaths/,
    );
    expect(checker).toMatch(
      /invalidContractExceptionPaths\s*=\s*\[[\s\S]*copyContractExceptionPaths/,
    );
  });

  it("keeps the registry collector restricted and reports malformed exceptions", async () => {
    const { collectContractExceptionCalls } = await import(
      "../../../../scripts/collect-api-contract-exceptions.mjs"
    );
    const fixture = [
      'const other = uncontractedApiPath("/outside/");',
      'const endpoints = { read: () => uncontractedApiPath("/inside/", {}, "inline reason") };',
      "const dynamic = uncontractedApiPath(template);",
    ].join("\n");
    expect(
      collectContractExceptionCalls(fixture, { registryOnly: true }),
    ).toEqual([{ value: "/inside/", inlineReason: true, line: 2 }]);
    expect(collectContractExceptionCalls(fixture)).toEqual([
      { value: "/outside/", inlineReason: false, line: 1 },
      { value: "/inside/", inlineReason: true, line: 2 },
      { value: "", inlineReason: false, line: 3 },
    ]);
  });
});
