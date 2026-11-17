// v2.1 g8 (access:grant): "+ Add people" never offers a workspace admin or
// owner (they always have full access), nor the caller or a granted person.
import { describe, expect, it } from "vitest";
import { addablePeople } from "../accessModel";

const people = [
  { id: "u1", name: "Maya Chen", organization_role: "Owner" },
  { id: "u2", name: "Asha Rao", organization_role: "Admin" },
  { id: "u3", name: "Alice Park", organization_role: "Member" },
  { id: "u4", name: "Ravi Menon", organization_role: "Member" },
  { id: "u5", name: "Viewer Vic", organization_role: "Viewer" },
];

describe("access: who can be added", () => {
  it("drops admins, owners, me and already-granted people", () => {
    const out = addablePeople(people, { granted: new Set(["u4"]), meId: "u1" });
    expect(out.map((p) => p.name)).toEqual(["Alice Park", "Viewer Vic"]);
  });

  it("an empty list stays empty", () => {
    expect(addablePeople(undefined)).toEqual([]);
  });
});
