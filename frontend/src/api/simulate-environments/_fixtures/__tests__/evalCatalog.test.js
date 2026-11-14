import { describe, it, expect } from "vitest";
import {
  EVAL_CATALOG,
  resolveEval,
  evalsForEnv,
} from "../evalCatalog";

describe("evalCatalog", () => {
  it("has unique catalog ids", () => {
    const ids = EVAL_CATALOG.map((e) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("resolves a known eval id to its catalog entry", () => {
    expect(resolveEval("task_success").name).toBe("Task success");
  });

  it("merges defaults for an unknown applied eval", () => {
    const resolved = resolveEval({ id: "x", name: "Y" });
    expect(resolved.id).toBe("x");
    expect(resolved.name).toBe("Y");
    expect(resolved.category).toBe("Custom");
    expect(resolved.type).toBe("LLM judge");
    expect(resolved.threshold).toBe(0.8);
    expect(resolved.color).toBeTruthy();
  });

  it("excludes browser-only evals for a voice environment", () => {
    const ids = evalsForEnv({ surface: "voice" }).map((e) => e.id);
    expect(ids).not.toContain("ui_grounding");
    expect(ids).toContain("task_success");
    expect(ids).toContain("interruption");
  });
});
