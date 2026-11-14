import path from "node:path";

import ts from "typescript";
import { describe, expect, it } from "vitest";

import { validateContractedResponse } from "../openapi-contract";
import { OPENAPI_CONTRACT } from "../openapi-contract.generated";

const unreachable = {
  status: "unreachable",
  error: "Gateway offline",
  last_health_check: "2026-01-02T03:04:15Z",
};

const validateError = (result) =>
  validateContractedResponse({
    config: {
      url: "/agentcc/gateways/default/health_check/",
      method: "post",
    },
    status: 400,
    data: { status: false, result },
  });

describe("gateway health error contract", () => {
  it.each(["Invalid request.", "", unreachable])(
    "accepts the existing string or completed-probe result: %j",
    (result) => {
      expect(validateError(result)).toMatchObject({ ok: true });
    },
  );

  it.each(["status", "error", "last_health_check"])(
    "requires %s on the object branch",
    (field) => {
      const result = { ...unreachable };
      delete result[field];
      expect(validateError(result)).toMatchObject({ ok: false });
    },
  );

  it.each(
    [
      {},
      [],
      1,
      false,
      null,
      { ...unreachable, status: "healthy" },
      { ...unreachable, error: {} },
      { ...unreachable, last_health_check: null },
    ].map((result) => [result]),
  )("rejects malformed results: %j", (result) => {
    expect(validateError(result)).toMatchObject({ ok: false });
  });

  it("documents a required date-time on the typed object branch", () => {
    const envelope = OPENAPI_CONTRACT.definitions.GatewayHealthErrorResponse;
    const resultRef = envelope.properties.result.$ref;
    const result = OPENAPI_CONTRACT.definitions[resultRef?.split("/").pop()];
    expect(result).toMatchObject({
      type: "object",
      "x-string-or-object": true,
      required: ["status", "error", "last_health_check"],
      properties: {
        status: { type: "string", enum: ["unreachable"] },
        error: { type: "string" },
        last_health_check: { type: "string", format: "date-time" },
      },
    });
  });

  it("generates string | typed probe in the TypeScript error envelope", () => {
    const schemaPath = path.resolve(
      "src/generated/api-contracts/api.schemas.ts",
    );
    const program = ts.createProgram([schemaPath], {
      strict: true,
      noEmit: true,
      skipLibCheck: true,
      types: [],
    });
    const checker = program.getTypeChecker();
    const source = program.getSourceFile(schemaPath);
    expect(program.getSyntacticDiagnostics(source)).toEqual([]);
    const envelope = checker
      .getExportsOfModule(checker.getSymbolAtLocation(source))
      .find((symbol) => symbol.name === "GatewayHealthErrorResponseApi");
    const propertyType = (type, name) =>
      checker.getTypeOfSymbolAtLocation(type.getProperty(name), source);
    const result = propertyType(
      checker.getDeclaredTypeOfSymbol(envelope),
      "result",
    );
    expect(result.isUnion()).toBe(true);
    const members = result.isUnion() ? result.types : [];
    expect(members).toHaveLength(2);
    expect(members.some((type) => type.flags === ts.TypeFlags.String)).toBe(
      true,
    );
    const object = members.find((type) => type.flags === ts.TypeFlags.Object);
    expect(object.getStringIndexType() === undefined).toBe(true);
    expect(
      object
        .getProperties()
        .map((field) => field.name)
        .sort(),
    ).toEqual(["error", "last_health_check", "status"]);
    for (const field of object.getProperties()) {
      expect(field.flags).toBe(ts.SymbolFlags.Property);
    }
    expect(checker.typeToString(propertyType(object, "status"))).toBe(
      '"unreachable"',
    );
    for (const field of ["error", "last_health_check"]) {
      expect(propertyType(object, field).flags).toBe(ts.TypeFlags.String);
    }
  });
});
