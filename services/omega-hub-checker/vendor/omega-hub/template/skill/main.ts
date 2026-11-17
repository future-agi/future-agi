// Lists the skills this entry ships and checks each SKILL.md has a name (equal to its folder) and a
// description. No model runs: a skill is files an agent reads.
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

export interface Skill {
  readonly folder: string;
  readonly name: string;
  readonly description: string;
}

/** The skills under `root`: one folder per skill, each with a SKILL.md and its frontmatter. */
export function readSkills(root = path.join(import.meta.dirname, "skills")): Skill[] {
  return readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
    .map((folder) => {
      const text = readFileSync(path.join(root, folder, "SKILL.md"), "utf8");
      const front = /^---\n([\s\S]*?)\n---/.exec(text)?.[1] ?? "";
      const field = (key: string) => new RegExp(`^${key}:\\s*(.+)$`, "m").exec(front)?.[1]?.trim() ?? "";
      return { folder, name: field("name"), description: field("description") };
    });
}

if (process.argv[1] === import.meta.filename) {
  const skills = readSkills();
  console.log(`${skills.length} skill(s)`);
  for (const skill of skills) console.log(`- ${skill.name}: ${skill.description}`);
}
