// HO D3: the filter panel's Collaboration fields (Assigned to, Comments,
// Language) over the live keys, with the server's people and languages.
import { describe, expect, it } from "vitest";
import {
  COLLAB_CATEGORY,
  COLLAB_FIELD,
  collabChipValue,
  collabFieldProperties,
  isCollabField,
} from "../collabFilterFields";
import { transformFilter } from "../common";
import { panelFilterToStore } from "../DevelopFilterBox";

const ASSIGNMENTS = {
  me: { id: "u1", name: "Maya Chen" },
  people: [
    { id: "u2", name: "Ravi Menon", open: 3, rows: 3 },
    { id: "u1", name: "Maya Chen", open: 5, rows: 5 },
  ],
  members: [{ id: "u3", name: "Asha Rao" }],
};
const TRANSLATE = {
  dataset_language_name: "English",
  languages: [
    { code: "en", name: "English" },
    { code: "es", name: "Spanish" },
  ],
};

describe("HO D3 · Collaboration filter fields", () => {
  it("offers the three fields with the server's people and languages", () => {
    const props = collabFieldProperties({
      assignments: ASSIGNMENTS,
      translate: TRANSLATE,
    });
    expect(props.map((p) => [p.id, p.name, p.category])).toEqual([
      ["system:assigned_to", "Assigned to", COLLAB_CATEGORY.key],
      ["system:open_comments", "Comments", COLLAB_CATEGORY.key],
      ["system:language", "Language", COLLAB_CATEGORY.key],
    ]);
    const assigned = props[0];
    // Me is `me`; the others by id, each once.
    expect(assigned.enumValues).toEqual(["me", "anyone", "nobody", "u3", "u2"]);
    expect(assigned.enumLabels.u2).toBe("Ravi Menon");
    expect(props[2].enumValues).toEqual(["en", "es", "__other__"]);
    expect(props[2].enumLabels.__other__).toBe(
      "Not English (the dataset's language)",
    );
  });

  it("leaves Language out when the translate read offers no language", () => {
    const props = collabFieldProperties({ assignments: ASSIGNMENTS });
    expect(props.map((p) => p.id)).toEqual([
      COLLAB_FIELD.assignedTo,
      COLLAB_FIELD.comments,
    ]);
    expect(isCollabField("system:language")).toBe(true);
    expect(isCollabField("c1")).toBe(false);
  });

  it("sends the live key the server answers: equals keeps (in), not equals drops (not_in)", () => {
    const keep = transformFilter(
      panelFilterToStore({
        field: "system:open_comments",
        fieldType: "string",
        operator: "in",
        value: "mentions_me",
      }),
    );
    expect(keep.column_id).toBe("system:open_comments");
    expect(keep.filter_config.filter_op).toBe("in");
    expect([keep.filter_config.filter_value].flat()).toEqual(["mentions_me"]);
    const drop = transformFilter(
      panelFilterToStore({
        field: "system:assigned_to",
        fieldType: "string",
        operator: "not_in",
        value: "nobody",
      }),
    );
    expect(drop.filter_config.filter_op).toBe("not_in");
  });

  it("shows a chip's value by its label", () => {
    const props = collabFieldProperties({
      assignments: ASSIGNMENTS,
      translate: TRANSLATE,
    });
    const chip = collabChipValue(
      {
        column_id: "system:language",
        filter_config: { filter_op: "in", filter_value: ["es"] },
      },
      props,
    );
    expect(chip.filter_config.filter_value).toEqual(["Spanish"]);
    const other = { column_id: "c1", filter_config: { filter_value: "x" } };
    expect(collabChipValue(other, props)).toBe(other);
  });
});
