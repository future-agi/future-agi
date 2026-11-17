/* eslint-env node */
// User C's community entry for the e2e run: `c-test/csv-answers`, a remix of
// future-agi/first-agent whose forecasts come from a CSV table, with an
// optional `--git` mode that shells out. Built at run time from the backend's
// first-agent fixture, so the demo output (and so R15) stays the same.
//
//   draft 1 says `shell: "None."`, which the checker flags as a disclosure gap;
//   draft 2 discloses the shell use, which is what the reviewer asks for.

const CSV_TABLE = `const CSV = \`city,forecast
lisbon,"sunny, 24 degrees"
oslo,"light rain, 9 degrees"\`;

// One row per city; the forecast column may hold a quoted comma.
export function parseCsv(text) {
  const [, ...rows] = text.trim().split("\\n");
  return Object.fromEntries(
    rows.map((row) => {
      const at = row.indexOf(",");
      return [row.slice(0, at), row.slice(at + 1).replace(/^"|"$/g, "")];
    })
  );
}

const FORECASTS = parseCsv(CSV);`;

const GIT_MODE = `
// Optional: print the local git version next to the answer (\`node main.mjs --git\`).
export function gitVersion() {
  return execFileSync("git", ["--version"], { encoding: "utf8" }).trim();
}
`;

const README = `# Weather answers from a CSV table

## What it does

A one-tool Omega agent whose forecasts come from a small CSV table instead of a hard-coded object. The \`get_weather\` tool parses the table once and looks the city up; the model asks for the tool and answers from its result.

Derived from future-agi/first-agent: same agent loop, a different data source and an optional git mode.

## Run it with no key

\`\`\`bash
omega hub run hub:c-test/csv-answers
\`\`\`

A scripted model plays the LLM. No key and no network.

## Run it with your key

\`\`\`bash
export OPENAI_API_KEY=...
omega hub run hub:c-test/csv-answers --live openai:gpt-4o-mini
\`\`\`

## How it works

- \`parseCsv\` turns the table into a city to forecast map.
- \`getWeather\` is the tool the model calls.
- \`node main.mjs --git\` also prints \`git --version\`, through \`execFileSync\`.

## Make it yours

- Replace the CSV with your own table.
- Add a column and return it from the tool.
`;

function disclosureFor(shellDisclosed) {
  return {
    summary: "Derived from future-agi/first-agent. Answers a weather request from a built-in CSV table with one tool.",
    network: "Keyless: none. Live: your chosen model vendor only (OpenAI, Anthropic or Vertex AI).",
    files: "Writes a run log in a temporary folder and removes it when done.",
    shell: shellDisclosed
      ? "Runs `git --version` only with --git; its test runs node main.mjs as a child process."
      : "None.",
    background: "None. It exits when the answer is printed.",
    credentials: "Reads the one model key named under Keys needed for a live run. Stores nothing.",
  };
}

const text = (data) => Buffer.from(data, "utf8");

// `badCover` uses colours outside the R1 palette, so the checks block it.
export function communityEntryFiles(baseFiles, { version, shellDisclosed, today, badCover = false }) {
  return baseFiles.map((file) => {
    const rel = file.name.replace(/^entry\//, "");
    const body = file.data.toString("utf8");
    if (rel === "omega.hub.json") {
      const manifest = JSON.parse(body);
      Object.assign(manifest, {
        id: "csv-answers",
        version,
        title: "Weather answers from a CSV table",
        tagline: "A one-tool agent that looks forecasts up in a CSV table, remixed from Your first agent.",
        author: { github: "c-test", name: "C Test", kind: "community" },
        tags: ["csv", "tool", "weather"],
        added: today,
        cover: badCover ? { emoji: "📊", from: "teal", to: "sky" } : { emoji: "📊", from: "green", to: "blue" },
        remix_of: "future-agi/first-agent@1.0.0",
        disclosure: disclosureFor(shellDisclosed),
      });
      return { ...file, data: text(`${JSON.stringify(manifest, null, 2)}\n`) };
    }
    if (rel === "main.mjs") {
      const changed = body
        .replace('import { runAgent, tool } from "@omega/core";', 'import { execFileSync } from "node:child_process";\nimport { runAgent, tool } from "@omega/core";')
        .replace(/const FORECASTS = \{[^\n]*\};/, CSV_TABLE)
        .replace("// 2. A model.", `${GIT_MODE}\n// 2. A model.`)
        .replace("if (isMain(import.meta.url)) {", 'if (isMain(import.meta.url)) {\n  if (process.argv.includes("--git")) console.log(gitVersion());');
      return { ...file, data: text(changed) };
    }
    if (rel === "README.md") return { ...file, data: text(README) };
    if (rel === "package.json") return { ...file, data: text(body.replace("omega-hub-first-agent", "omega-hub-csv-answers")) };
    if (rel === "demo.cast") return { ...file, data: text(body.replace("first-agent (keyless)", "csv-answers (keyless)")) };
    return file;
  });
}
