import fs from "node:fs";
import path from "node:path";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as surface from "../api-surface";
import { API_CONTRACT_EXCEPTIONS } from "../api-contract-exceptions";
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
const source = () => fs.readFileSync(path.resolve(ROW_SOURCE), "utf8");

afterEach(() => {
  vi.restoreAllMocks();
  setLaunchOverrideForTest(null);
});

describe("the contracted dataset row read", () => {
  it("uses the public GET without an exception registration", () => {
    expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(ROW_READ_TEMPLATE);
    expect(surface.isContractedApiPath(ROW_READ_TEMPLATE)).toBe(true);
    expect(surface.getContractedApiMethods(ROW_READ_TEMPLATE)).toEqual(["get"]);
  });

  it("formats through the contracted helper and encodes both ids", () => {
    const format = vi.spyOn(surface, "apiPath");
    const exceptionFormat = vi.spyOn(surface, "uncontractedApiPath");
    expect(rowReadRequest("dataset one", "row/one")).toEqual({
      method: "GET",
      path: "/model-hub/datasets/dataset%20one/rows/row%2Fone/",
    });
    expect(format).toHaveBeenCalledTimes(1);
    expect(format).toHaveBeenCalledWith(ROW_READ_TEMPLATE, {
      dataset_id: "dataset one",
      row_id: "row/one",
    });
    expect(exceptionFormat).not.toHaveBeenCalled();
    expect(rowReadRequest(null, "row/one")).toBeNull();
    expect(rowReadRequest("dataset one", "")).toBeNull();
  });

  it("offers API copy text with the shipped defaults", () => {
    setLaunchOverrideForTest(null);
    expect(rowApiCopyProps("dataset", "row")).toEqual({
      apiPath: "GET /model-hub/datasets/dataset/rows/row/",
      apiCall: expect.stringContaining("/model-hub/datasets/dataset/rows/row/"),
    });
  });

  it("offers no API copy text when a build lacks the contracted GET", () => {
    vi.spyOn(surface, "getContractedApiMethods").mockReturnValue([]);
    expect(rowReadRequest("dataset", "row")).toBeNull();
    expect(rowApiCopyProps("dataset", "row", true)).toEqual({});
    setLaunchOverrideForTest({ launch: false });
    expect(rowApiCopyProps("dataset", "row")).toEqual({});
  });

  it("honours an explicitly hidden API surface", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      showUnreleased: false,
    });
    expect(rowApiCopyProps("dataset", "row")).toEqual({});
  });

  it("refuses row reads and siblings through the exception helper", () => {
    const sibling = "/model-hub/datasets/{dataset_id}/rows/";
    for (const template of [ROW_READ_TEMPLATE, sibling]) {
      expect(surface.isApiContractExceptionPath(template)).toBe(false);
      expect(() => surface.uncontractedApiPath(template)).toThrow(
        "API contract exception path is not registered",
      );
    }
    expect(() => surface.apiPath(ROW_READ_TEMPLATE)).toThrow(
      'Missing API path param "dataset_id"',
    );
  });

  it("has a static apiPath call backed by the generated surface", () => {
    const templates = [];
    traverse(parse(source(), { sourceType: "module" }), {
      CallExpression(nodePath) {
        const { callee, arguments: args } = nodePath.node;
        if (callee.type !== "Identifier" || callee.name !== "apiPath") return;
        const template =
          args[0]?.type === "StringLiteral" ? args[0].value : null;
        templates.push(template);
        expect(Object.hasOwn(API_SURFACE_PATHS, template)).toBe(true);
      },
    });
    expect(templates).toEqual([ROW_READ_TEMPLATE]);
  });

  it("no longer counts the copy helper as an exception caller", async () => {
    const { collectContractExceptionCalls } = await import(
      "../../../../scripts/collect-api-contract-exceptions.mjs"
    );
    expect(collectContractExceptionCalls(source())).toEqual([]);
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
