import assert from "node:assert/strict";
import { test } from "node:test";
import { readSkills } from "./main.ts";

test("every skill names its folder and says when to use it", () => {
  const skills = readSkills();
  assert.ok(skills.length > 0);
  for (const skill of skills) {
    assert.equal(skill.name, skill.folder);
    assert.ok(skill.description.length >= 20, `${skill.folder} has a description`);
  }
});
