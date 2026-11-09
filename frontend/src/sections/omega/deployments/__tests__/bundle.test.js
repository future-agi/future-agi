import { describe, expect, it } from "vitest";

import {
  BundleError,
  bundleFolder,
  compareBundlePaths,
  deploymentNameFrom,
  exclusionFor,
  gunzip,
  gzip,
  LANGGRAPH_PROJECT_FILE,
  readBundleArchive,
  readLangGraphConfig,
  sha256Hex,
} from "../lib/bundle";
import { readTarMembers, splitTarPath, writeTar } from "../lib/tar";
import fixture from "./fixtures/serve-fixture.json";

// Files as a folder picker hands them over: webkitRelativePath starts with
// the picked folder's own name.
function picked(files, folder = "serve-fixture") {
  return Object.entries(files).map(([path, text]) => {
    const file = new File([text], path.split("/").pop());
    Object.defineProperty(file, "webkitRelativePath", {
      value: `${folder}/${path}`,
    });
    return file;
  });
}

const fromBase64 = (text) =>
  Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

describe("browser bundle of a project folder", () => {
  it("writes the same tar as `omega deploy` for the same files (contentSha256 matches the CLI's)", async () => {
    // Reverse the pick order: the bundler must sort, not trust the picker.
    const files = Object.fromEntries(Object.entries(fixture.files).reverse());
    const bundle = await bundleFolder(picked(files));
    expect(bundle.contentSha256).toBe(fixture.cli.contentSha256);
    expect(bundle.files.map((f) => f.path)).toEqual([
      "entries/env-probe.ts",
      "entries/greet.ts",
      "entries/wait.ts",
      "omega.studio.json",
    ]);
    // The gzip bytes are ours; the digest and size describe exactly them.
    expect(bundle.sha256).toBe(await sha256Hex(bundle.archive));
    expect(bundle.size).toBe(bundle.archive.length);
    expect(await sha256Hex(await gunzip(bundle.archive))).toBe(
      fixture.cli.contentSha256,
    );
    expect(bundle.manifest.name).toBe("serve-fixture");
    expect(bundle.manifest.entries.map((e) => e.id)).toEqual([
      "greet",
      "env-probe",
      "wait",
    ]);
  });

  it("leaves out what the CLI leaves out and lists it", async () => {
    const bundle = await bundleFolder(
      picked({
        ...fixture.files,
        ".env": "SECRET=1",
        ".env.example": "SECRET=",
        "node_modules/x/index.js": "x",
        ".omega/state.json": "{}",
        "certs/server.pem": "pem",
        ".DS_Store": "",
      }),
    );
    expect(bundle.contentSha256).not.toBe(fixture.cli.contentSha256);
    expect(bundle.files.map((f) => f.path)).toContain(".env.example");
    expect(bundle.excluded).toEqual([
      { path: ".DS_Store", reason: "system file" },
      { path: ".env", reason: "may hold secrets" },
      { path: ".omega/state.json", reason: "local state" },
      { path: "certs/server.pem", reason: "may hold secrets" },
      { path: "node_modules/x/index.js", reason: "dependencies" },
    ]);
  });

  it("refuses a folder without omega.studio.json at its top level", async () => {
    const nested = Object.fromEntries(
      Object.entries(fixture.files).map(([p, t]) => [`inner/${p}`, t]),
    );
    await expect(bundleFolder(picked(nested))).rejects.toThrow(
      /no omega\.studio\.json at its top level/,
    );
  });

  it("refuses a manifest that lists no entries", async () => {
    await expect(
      bundleFolder(
        picked({ "omega.studio.json": '{"name":"x","entries":[]}' }),
      ),
    ).rejects.toThrow(BundleError);
  });
});

describe("a bundle written by `omega deploy bundle --out`", () => {
  it("is sent byte for byte with the CLI's own digests", async () => {
    const bytes = fromBase64(fixture.cli.archiveBase64);
    const bundle = await readBundleArchive(new File([bytes], "p.tar.gz"));
    expect(bundle.sha256).toBe(fixture.cli.sha256);
    expect(bundle.contentSha256).toBe(fixture.cli.contentSha256);
    expect(bundle.size).toBe(fixture.cli.size);
    expect(bundle.source).toBe("archive");
    expect(bundle.manifest.entries).toHaveLength(3);
  });

  it("refuses a file that is not gzip", async () => {
    await expect(
      readBundleArchive(new File(["not gzip"], "p.tar.gz")),
    ).rejects.toThrow(/not a gzip-compressed tar/);
  });
});

// The upstream LangGraph.js template's langgraph.json, byte for byte
// (langchain-ai/new-langgraphjs-project 04905cdec, the OSS adapter's fixture).
const UPSTREAM_LANGGRAPH_JSON = `{
  "node_version": "20",
  "graphs": {
    "agent": "./src/agent/graph.ts:graph"
  },
  "env": ".env",
  "dependencies": ["."],
  "image_distro": "wolfi"
}
`;
const langgraphFiles = {
  "langgraph.json": UPSTREAM_LANGGRAPH_JSON,
  "src/agent/graph.ts": "export const graph = {};\n",
  ".env": "OPENAI_API_KEY=never-bundled\n",
};

describe("a LangGraph.js project (root langgraph.json, no omega.studio.json)", () => {
  it("bundles the folder with its graphs as entries and the folder's name, leaving .env out", async () => {
    const bundle = await bundleFolder(
      picked(langgraphFiles, "new-langgraphjs-project"),
    );
    expect(bundle.manifest).toEqual({
      file: LANGGRAPH_PROJECT_FILE,
      name: "new-langgraphjs-project",
      entries: [{ id: "agent", label: null, kind: "langgraph" }],
    });
    expect(bundle.files.map((f) => f.path)).toEqual([
      "langgraph.json",
      "src/agent/graph.ts",
    ]);
    expect(bundle.excluded).toEqual([
      { path: ".env", reason: "may hold secrets" },
    ]);
    const members = readTarMembers(await gunzip(bundle.archive));
    expect(members.map((m) => m.path)).toEqual([
      "langgraph.json",
      "src/agent/graph.ts",
    ]);
    expect(bundle.contentSha256).toBe(
      await sha256Hex(await gunzip(bundle.archive)),
    );
  });

  it("names a graph by its description when langgraph.json gives one", () => {
    const bytes = new TextEncoder().encode(
      JSON.stringify({
        graphs: { agent: { path: "./a.ts", description: "The agent" } },
      }),
    );
    expect(readLangGraphConfig(bytes).entries).toEqual([
      { id: "agent", label: "The agent", kind: "langgraph" },
    ]);
  });

  it("reads omega.studio.json when a folder has both, as `omega deploy` does", async () => {
    const both = { ...fixture.files, "langgraph.json": "{not json" };
    const bundle = await bundleFolder(picked(both));
    expect(bundle.manifest.file).toBe("omega.studio.json");
    expect(bundle.manifest.entries).toHaveLength(3);
    expect(bundle.files.map((f) => f.path)).toContain("langgraph.json");
  });

  it("refuses a langgraph.json that is not JSON or names no graphs", async () => {
    for (const [text, message] of [
      ["{ nope", "langgraph.json is not valid JSON."],
      ["[]", "langgraph.json names no graphs."],
      ['{"graphs":{}}', "langgraph.json names no graphs."],
      ['{"graphs":["./a.ts:graph"]}', "langgraph.json names no graphs."],
    ]) {
      await expect(
        bundleFolder(picked({ ...langgraphFiles, "langgraph.json": text })),
      ).rejects.toThrow(new BundleError(message));
    }
  });

  it("keeps the refusal for a folder with neither file word for word", async () => {
    await expect(
      bundleFolder(picked({ "src/index.ts": "export {};\n" }, "app")),
    ).rejects.toThrow(
      new BundleError(
        "This folder has no omega.studio.json at its top level, so it is not an Omega project. Pick the folder that holds it.",
      ),
    );
    await expect(
      bundleFolder(
        picked({ "inner/langgraph.json": UPSTREAM_LANGGRAPH_JSON }, "app"),
      ),
    ).rejects.toThrow(/no omega\.studio\.json at its top level/);
  });

  it("accepts a .tar.gz with langgraph.json at its root, with no name of its own", async () => {
    const enc = new TextEncoder();
    const tar = writeTar([
      {
        path: "langgraph.json",
        data: enc.encode(UPSTREAM_LANGGRAPH_JSON),
        executable: false,
      },
      {
        path: "src/agent/graph.ts",
        data: enc.encode("export const graph = {};\n"),
        executable: false,
      },
    ]);
    const archive = await gzip(tar);
    const bundle = await readBundleArchive(new File([archive], "lg.tar.gz"));
    expect(bundle.manifest).toEqual({
      file: "langgraph.json",
      name: null,
      entries: [{ id: "agent", label: null, kind: "langgraph" }],
    });
    const none = await gzip(
      writeTar([
        { path: "index.ts", data: enc.encode("x"), executable: false },
      ]),
    );
    await expect(
      readBundleArchive(new File([none], "none.tar.gz")),
    ).rejects.toThrow(
      new BundleError(
        "This archive has no omega.studio.json at its root, so it is not an Omega project bundle.",
      ),
    );
  });
});

describe("tar details", () => {
  it("splits long paths into ustar prefix and name", () => {
    const long = `${"d".repeat(120)}/file.ts`;
    expect(splitTarPath(long)).toEqual({
      prefix: "d".repeat(120),
      name: "file.ts",
    });
    expect(() => splitTarPath("../x")).toThrow();
    expect(() => splitTarPath("/abs")).toThrow();
  });

  it("reads back what it writes and refuses links", () => {
    const data = new TextEncoder().encode("hello");
    const tar = writeTar([{ path: "a/b.txt", data, executable: true }]);
    const [member, ...rest] = readTarMembers(tar);
    expect(rest).toEqual([]);
    expect(member.path).toBe("a/b.txt");
    expect(Array.from(member.data)).toEqual(Array.from(data));
    const link = writeTar([
      { path: "l", data: new Uint8Array(), executable: false },
    ]);
    link[156] = 0x32; // "2": symbolic link
    // Recompute the checksum so only the type is wrong.
    link.fill(0x20, 148, 156);
    let sum = 0;
    for (const byte of link.subarray(0, 512)) sum += byte;
    new TextEncoder()
      .encode(`${sum.toString(8).padStart(6, "0")}\0 `)
      .forEach((b, i) => {
        link[148 + i] = b;
      });
    expect(() => readTarMembers(link)).toThrow(/link or special file/);
  });

  it("orders paths like a directory walk", () => {
    const paths = ["b.ts", "a/z.ts", "a.ts", "a/b/c.ts", "A.ts"];
    expect([...paths].sort(compareBundlePaths)).toEqual([
      "A.ts",
      "a/b/c.ts",
      "a/z.ts",
      "a.ts",
      "b.ts",
    ]);
  });

  it("derives the CLI's default deployment name", () => {
    expect(deploymentNameFrom("Serve Fixture!")).toBe("serve-fixture");
    expect(deploymentNameFrom("42 bots")).toBe("bots");
    expect(deploymentNameFrom("")).toBe("omega-project");
    expect(exclusionFor("src/.env.local")).toBe("may hold secrets");
  });
});
