/* eslint-env node */
/* eslint-disable no-console */
// Keeps design-system lint opt-outs rare and explained.
//
// An opt-out is `eslint-disable-next-line no-restricted-syntax -- ds: <reason>`.
// This check fails when:
//   - an opt-out has no `-- ds: <reason>`, or a reason outside ALLOWED_REASONS;
//   - a file turns the rule off for the whole file and isn't in WHOLE_FILE_EXCEPTIONS;
//   - the number of opt-outs grows past MAX_DS_OPTOUTS.
// Lower MAX_DS_OPTOUTS (and trim WHOLE_FILE_EXCEPTIONS) when you remove opt-outs.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcRoot = path.resolve(__dirname, "..", "src");

// Chart and series colours are not on this list: use theme.palette.data
// (seriesColor / seriesColors in src/theme/data-colors.js).
export const ALLOWED_REASONS = {
  brand: "a third-party or Future AGI brand colour or logo",
  encoding: "a fixed colour that names a type everywhere it appears (span, label, message role)",
  "user-picked": "a colour a user chose and the product stores",
  illustration: "decorative artwork or animation",
  "editor theme": "a code editor's syntax colours",
  avatar: "the avatar palette",
  "status dot size": "the size of a status dot",
  layout: "a layout value the rule mistakes for styling",
  "error state": "a menu option shown in red because it can't be used",
};

// Files that are whole tables of exception colours. This list can only shrink.
export const WHOLE_FILE_EXCEPTIONS = [
  "components/animate/variants/background.js",
  "components/label-type-chip/LabelTypeChip.jsx",
  "components/logo/logo.jsx",
  "components/multi-track-audio-player/MultiTrackAudioPlayer.jsx",
  "components/scenarios/helper.jsx",
  "components/traceDetail/spanTypeConfig.js",
  "components/traceDetail/tagUtils.js",
  "sections/evals/EvalPlayground/BottomEvaluationSection/BottomEvaluationSection.jsx",
  "sections/evals/components/CodeEditor.jsx",
  "sections/evals/components/EvalsListView.jsx",
  "sections/experiment-detail/ExperimentData/Common.js",
  "sections/project/TagEditor.jsx",
  "sections/projects/LLMTracing/GraphSection/AgentGraph.jsx",
  "sections/projects/LLMTracing/GraphSection/agentPathUtils.js",
  "sections/simulate/environments/buildEnvironment/buildTones.js",
];

export const MAX_DS_OPTOUTS = 81;

const RULE = "no-restricted-syntax";
const DISABLE = /eslint-disable(-next-line|-line)?\s+([^*\n]*?)(?:\s*\*\/|\s*$)/;

const walk = (dir) =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      return entry.name === "node_modules" ? [] : walk(full);
    }
    return /\.(js|jsx)$/.test(entry.name) ? [full] : [];
  });

const reasonOf = (rest) => {
  const match = rest.match(/--\s*ds:\s*(.+)$/);
  return match ? match[1].trim() : null;
};

const allowed = (reason) =>
  Object.keys(ALLOWED_REASONS).some(
    (key) => reason === key || reason.startsWith(`${key} (`),
  );

const problems = [];
let optouts = 0;

for (const file of walk(srcRoot)) {
  const rel = path.relative(srcRoot, file);
  const lines = fs.readFileSync(file, "utf8").split("\n");
  lines.forEach((line, i) => {
    const match = line.match(DISABLE);
    if (!match || !match[2].includes(RULE)) return;
    const [, kind, rest] = match;
    const where = `src/${rel}:${i + 1}`;
    const reason = reasonOf(rest);
    if (!kind) {
      if (!WHOLE_FILE_EXCEPTIONS.includes(rel)) {
        problems.push(
          `${where}  turns the design-system rules off for the whole file; opt out per line instead`,
        );
      }
      return;
    }
    optouts += 1;
    if (!reason) {
      problems.push(`${where}  opt-out without a reason; add "-- ds: <reason>"`);
    } else if (!allowed(reason)) {
      problems.push(
        `${where}  "${reason}" isn't an allowed reason (${Object.keys(ALLOWED_REASONS).join(", ")})`,
      );
    }
  });
}

if (optouts > MAX_DS_OPTOUTS) {
  problems.push(
    `${optouts} design-system opt-outs, above the limit of ${MAX_DS_OPTOUTS}. Use a theme token or a shared component instead of a new opt-out.`,
  );
}

for (const p of problems) console.log(p);
console.log(
  `\nds:optouts: ${optouts} opt-out(s), limit ${MAX_DS_OPTOUTS}, ${problems.length} problem(s).`,
);
process.exit(problems.length ? 1 : 0);
