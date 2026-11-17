// options-truth L5 G5 (extra:one-command-builder, snip:consistency): one
// builder prints every command, from exactly the request the screen sends.
import { describe, expect, it } from "vitest";
import {
  FI_AUTH_HEADERS,
  FI_BASE_URL,
  fiRequest,
  requestSnippets,
  toPythonLiteral,
} from "../requestSnippets";
import { FI_BASE_URL as UPLOAD_BASE } from "src/sections/develop-detail/Experiment/uploadSnippets";
import {
  IMPORTS_PATH,
  apiCallSnippets,
} from "src/sections/common/AddData/mappingModel";
import {
  buildApiSnippets,
  iteratePath,
} from "src/sections/experiment-detail/iterate/CopyAsApiDialog";
import { toIterateBody } from "src/api/develop/r2";

/** The JSON body a cURL command posts (the -d '...' argument). */
const curlBody = (curl) =>
  JSON.parse(curl.match(/-d '([\s\S]*)'$/)[1].replace(/'\\''/g, "'"));
/** The JSON body a TypeScript snippet posts (const body = …;). */
const tsBody = (ts) => JSON.parse(ts.match(/const body = ([\s\S]*?);\n\n/)[1]);
const headerNames = (curl) =>
  [...curl.matchAll(/-H "([^:]+):/g)].map((m) => m[1]);

describe("requestSnippets", () => {
  it("keeps one API host, re-exported by uploadSnippets", () => {
    expect(UPLOAD_BASE).toBe(FI_BASE_URL);
  });

  it("prints the body exactly, with the same auth headers in every language", () => {
    const body = { a: "it's", n: 3, ok: true, none: null, list: [1, "x"] };
    const req = fiRequest("/p/", body);
    const { curl, python, typescript } = requestSnippets(req);
    expect(curl.split("\n")[0]).toBe(
      "# Create a personal API key pair at /dashboard/keys",
    );
    expect(
      curl.split("\n")[1].startsWith(`curl -X POST "${FI_BASE_URL}/p/"`),
    ).toBe(true);
    expect(curlBody(curl)).toEqual(body);
    expect(tsBody(typescript)).toEqual(body);
    expect(headerNames(curl)).toEqual(["X-Api-Key", "X-Secret-Key"]);
    Object.entries(FI_AUTH_HEADERS).forEach(([h, env]) => {
      expect(curl).toContain(`-H "${h}: $${env}"`);
      expect(python).toContain(`"${h}": os.environ["${env}"]`);
      expect(typescript).toContain(`"${h}": process.env.${env}!`);
    });
    expect(python).toContain(`body = ${toPythonLiteral(body)}`);
    expect(python).toContain('"ok": True');
    expect(python).toContain('"none": None');
    expect(python).toContain('"a": "it\'s"');
  });

  it("does not turn true / null inside strings into Python words", () => {
    expect(toPythonLiteral({ s: "null true false" })).toContain(
      '"null true false"',
    );
  });
});

describe("the two Copy as API screens print what they send", () => {
  it("AddData: the IM1 commit useImportCommit sends", () => {
    const commitBody = {
      source: { kind: "sessions", project_id: "p1", session_ids: ["s1"] },
      target: { dataset_id: "ds1" },
      shape: "task",
      temp_ids: ["imp-1", "imp-3"],
    };
    const sent = { ...commitBody, op: "commit" }; // api/develop/r2 useImportCommit
    const { curl, typescript } = apiCallSnippets(commitBody);
    expect(curl).toContain(`"${FI_BASE_URL}${IMPORTS_PATH}"`);
    expect(curlBody(curl)).toEqual(sent);
    expect(tsBody(typescript)).toEqual(sent);
  });

  it("Iterate dock: the I1 body useIterate sends", () => {
    const dockBody = {
      parent_run_id: "run-1",
      scope: { type: "rows", row_ids: ["r1", "r2"] },
      trials: 3,
      change: [{ axis: "prompt", text: "Keep the prefix" }],
    };
    const sent = toIterateBody(dockBody);
    const { curl, python, typescript } = buildApiSnippets("exp-1", dockBody);
    expect(curl).toContain(`"${FI_BASE_URL}${iteratePath("exp-1")}"`);
    expect(curlBody(curl)).toEqual(sent);
    expect(tsBody(typescript)).toEqual(sent);
    // Same header names as AddData's command.
    expect(headerNames(curl)).toEqual(
      headerNames(apiCallSnippets({ target: { dataset_id: "d" } }).curl),
    );
    expect(python).toContain("requests.request(");
  });
});
