import { describe, expect, it } from "vitest";
import {
  ID_MAX,
  NAME_MAX,
  ROLE_MAX,
  freeIdFrom,
  idFrom,
  idProblemOf,
  isValidForm,
  problemsOf,
} from "../actorForm";

describe("idFrom", () => {
  it("folds accents and joins words with underscores", () => {
    expect(idFrom("José Núñez")).toBe("jose_nunez");
    expect(idFrom("Zoë  O'Brien-Smith")).toBe("zoe_o_brien_smith");
  });
  it("trims stray separators and lowercases", () => {
    expect(idFrom("  --Manager!  ")).toBe("manager");
    expect(idFrom("Team 2 lead")).toBe("team_2_lead");
  });
  it("is empty when the name has nothing to build one from", () => {
    expect(idFrom("")).toBe("");
    expect(idFrom("!!!")).toBe("");
    expect(idFrom(null)).toBe("");
  });
  it("stops at 64 characters without a trailing underscore", () => {
    const id = idFrom(`${"a".repeat(63)} b`);
    expect(id.length).toBeLessThanOrEqual(ID_MAX);
    expect(id.endsWith("_")).toBe(false);
  });
});

describe("freeIdFrom", () => {
  it("returns the id when it is free", () => {
    expect(freeIdFrom("Manager", ["supervisor"])).toBe("manager");
  });
  it("numbers it when it is taken", () => {
    expect(freeIdFrom("Manager", ["manager"])).toBe("manager_2");
    expect(freeIdFrom("Manager", ["manager", "manager_2"])).toBe("manager_3");
  });
});

describe("idProblemOf", () => {
  it("accepts 1 to 64 lowercase letters, digits and underscores", () => {
    expect(idProblemOf("a")).toBeNull();
    expect(idProblemOf("jose_nunez_2")).toBeNull();
    expect(idProblemOf("a".repeat(64))).toBeNull();
  });
  it("refuses everything else", () => {
    expect(idProblemOf("")).toMatch(/id/i);
    expect(idProblemOf("a".repeat(65))).toMatch(/64/);
    expect(idProblemOf("Manager")).toMatch(/lowercase/i);
    expect(idProblemOf("act-supervisor")).toMatch(/lowercase/i);
  });
  it("refuses an id another actor has", () => {
    expect(idProblemOf("manager", ["manager"])).toMatch(/already used/);
  });
});

describe("problemsOf", () => {
  const ok = { name: "Manager", id: "manager", role: "approver" };
  it("needs a name, a valid free id and short enough text", () => {
    expect(problemsOf(ok)).toEqual({});
    expect(isValidForm(ok)).toBe(true);
    expect(problemsOf({ ...ok, name: "  " }).name).toBeTruthy();
    expect(problemsOf({ ...ok, name: "x".repeat(NAME_MAX + 1) }).name).toMatch(
      String(NAME_MAX),
    );
    expect(problemsOf({ ...ok, role: "x".repeat(ROLE_MAX + 1) }).role).toMatch(
      String(ROLE_MAX),
    );
    expect(problemsOf({ ...ok, id: "Bad Id" }).id).toBeTruthy();
    expect(problemsOf(ok, { takenIds: ["manager"] }).id).toMatch(/already/);
  });
  it("leaves the id of an existing actor alone", () => {
    expect(
      problemsOf({ ...ok, id: "act-supervisor" }, { checkId: false }),
    ).toEqual({});
  });
  it("accepts a name and role at their limits", () => {
    expect(
      isValidForm({
        ...ok,
        name: "x".repeat(NAME_MAX),
        role: "y".repeat(ROLE_MAX),
      }),
    ).toBe(true);
  });
});
