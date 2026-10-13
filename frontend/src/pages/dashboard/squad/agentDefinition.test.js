import { describe, expect, it } from "vitest";
import {
  exportAgentDefinition,
  importAgentDefinition,
  importAgentDefinitionZip,
  importedDefinitionDraft,
  MAX_DEFINITION_BYTES,
} from "./agentDefinition";

const authored = {
  name: "Researcher",
  role: "Analyst",
  purpose: "Find evidence",
  responsibilities: ["Explain\nwhy", "Cite sources"],
  workMode: "always_on",
  examples: [{ prompt: "Why?", response: "Evidence first." }],
};

describe("portable authored agent definitions", () => {
  it("round trips authored fields while excluding native identities, secrets and runtime state", () => {
    const exported = exportAgentDefinition({
      ...authored,
      id: "private-agent-id",
      accountId: "private-account",
      status: "working",
      environment: { id: "private-env", revision: 3 },
      connectorBindings: [{ id: "private-binding", secret: "private-secret" }],
      metadata: { credential: "private-key" },
    });
    expect(exported).not.toMatch(/private-|working/);
    const imported = importAgentDefinition(exported);
    expect(imported).toEqual({
      definition: authored,
      setupRequired: { environment: true, connections: true },
    });
    expect(importedDefinitionDraft(imported)).toMatchObject({
      environmentId: "",
      environmentRevision: "",
      sourceResponsibilities: authored.responsibilities,
      examples: authored.examples,
    });
  });
  it.each([
    (value) => {
      value.definition.connectorBindingIds = ["unknown"];
    },
    (value) => {
      value.credentials = { token: "secret" };
    },
    (value) => {
      value.definition.responsibilities = [3];
    },
    (value) => {
      value.definition.workMode = "run_now";
    },
    (value) => {
      value.definition.examples = [{ prompt: "missing response" }];
    },
    (value) => {
      value.definition.purpose = "x".repeat(4001);
    },
    (value) => {
      value.schemaVersion = "unknown";
    },
    (value) => {
      value.definition.__proto__ = { name: "polluted" };
      delete value.definition.name;
    },
  ])(
    "rejects malformed or unsupported fields instead of silently normalizing case %#",
    (mutate) => {
      const value = JSON.parse(exportAgentDefinition(authored));
      mutate(value);
      expect(() => importAgentDefinition(JSON.stringify(value))).toThrow();
    },
  );
  it("bounds files and requires complete server examples before exporting", () => {
    expect(() =>
      importAgentDefinition(" ".repeat(MAX_DEFINITION_BYTES + 1)),
    ).toThrow(/256 KiB/);
    expect(() =>
      exportAgentDefinition({ ...authored, examples: undefined }),
    ).toThrow(/complete authoring/);
    const duplicate = importedDefinitionDraft(
      importAgentDefinition(
        exportAgentDefinition({ ...authored, name: "学".repeat(66) }),
      ),
      true,
    );
    expect(new TextEncoder().encode(duplicate.name).length).toBeLessThanOrEqual(
      200,
    );
    expect(duplicate.name).toMatch(/ copy$/);
  });
  it("round trips a bounded ZIP manifest and rejects malformed archives", () => {
    const archive = Uint8Array.from(
      atob(
        "UEsDBBQAAAAIAAJFTV0OFnX04gAAAEEBAAAQAAAAb21lZ2EtYWdlbnQuanNvbkVQPU/DQAz9K5XnBMSaBSFUNpYOMECFTOI0Fne+6/mSNIry3/FVlTr6+X34eQVtB/L4QUk5CDQQPJ2w1vOIXY0nklx31LNwtvXj9AQV3GdoVhD0ZLIDKWEyr2SMFFzBXgTdotmAOKYYtGBvLN2OJu5IWipU0hhE+ZedWZJC8wX7S3TI8i3zsBjllTPtNIyptfWxgjmkv/fQFTd0My76Y5dUQBf00V0dVogp+JiN8Tksz/eYotnfwnc9J80PsB23CpTyGA90HjlRV2qRTJyCeHsAND06pQraIEJtKW4hV2zb/gFQSwECFAAUAAAACAACRU1dDhZ19OIAAABBAQAAEAAAAAAAAAAAAAAAAAAAAAAAb21lZ2EtYWdlbnQuanNvblBLBQYAAAAAAQABAD4AAAAQAQAAAAA=",
      ),
      (char) => char.charCodeAt(0),
    );
    const imported = importAgentDefinitionZip(archive);
    expect(imported.definition).toEqual(authored);
    expect(() => importAgentDefinitionZip(new Uint8Array([1, 2, 3]))).toThrow(
      /valid Omega Squad definition ZIP/,
    );
  });
});
