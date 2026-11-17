// Saved connections keep a URL and a label, nothing else: whatever else an
// entry in storage carries (a key, a token) is dropped on every read and write.
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  ACTIVE_CONNECTION_KEY,
  MAX_SAVED_CONNECTIONS,
  SAVED_CONNECTIONS_KEY,
  clearActiveConnection,
  forgetConnection,
  readActiveConnection,
  readSavedConnections,
  rememberConnection,
  renameConnection,
  writeActiveConnection,
} from "../saved-connections";
import { resetStudioTestState, storages } from "./studio-test-utils";

const A = "http://127.0.0.1:4191";
const B = "http://localhost:4300";
const [local, session] = [() => storages()[0], () => storages()[1]];
const seed = (list) =>
  local().setItem(SAVED_CONNECTIONS_KEY, JSON.stringify(list));
const stored = () => JSON.parse(local().getItem(SAVED_CONNECTIONS_KEY));

beforeEach(resetStudioTestState);
afterEach(resetStudioTestState);

describe("saved connections", () => {
  it("reads URL and label only, whatever else an entry carries", () => {
    seed([
      { url: A, label: "Support", token: "secret-a", t: "x" },
      { url: `${B}/`, label: "", key: "secret-b" },
      { url: "https://studio.example.com", label: "remote" },
      "not an entry",
    ]);
    expect(readSavedConnections()).toEqual([
      { url: A, label: "Support" },
      { url: B, label: "localhost:4300" },
    ]);
  });

  it("writes URL and label only when another entry is remembered, renamed or forgotten", () => {
    seed([
      { url: A, label: "Support", token: "secret-a" },
      { url: B, label: "Triage", key: "secret-b" },
    ]);
    rememberConnection(B);
    expect(stored()).toEqual([
      { url: B, label: "Triage" },
      { url: A, label: "Support" },
    ]);
    seed([
      { url: A, label: "Support", token: "secret-a" },
      { url: B, label: "Triage", key: "secret-b" },
    ]);
    renameConnection(A, "Mine");
    expect(stored()).toEqual([
      { url: A, label: "Mine" },
      { url: B, label: "Triage" },
    ]);
    seed([
      { url: A, label: "Support", token: "secret-a" },
      { url: B, label: "Triage", key: "secret-b" },
    ]);
    forgetConnection(A);
    expect(stored()).toEqual([{ url: B, label: "Triage" }]);
    expect(local().getItem(SAVED_CONNECTIONS_KEY)).not.toMatch(/secret/);
  });

  it("keeps at most a few, newest first, without duplicates", () => {
    for (let port = 5000; port < 5000 + MAX_SAVED_CONNECTIONS + 3; port += 1) {
      rememberConnection(`http://127.0.0.1:${port}`);
    }
    rememberConnection("http://127.0.0.1:5001/");
    const list = readSavedConnections();
    expect(list).toHaveLength(MAX_SAVED_CONNECTIONS);
    expect(list[0].url).toBe("http://127.0.0.1:5001");
    expect(new Set(list.map((item) => item.url)).size).toBe(list.length);
  });

  it("remembers the server this tab shows as a loopback URL only", () => {
    writeActiveConnection(`${A}/?x=1#t=secret`);
    expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBe(A);
    expect(readActiveConnection()).toBe(A);
    writeActiveConnection("https://studio.example.com");
    expect(readActiveConnection()).toBe(A);
    session().setItem(ACTIVE_CONNECTION_KEY, "https://studio.example.com");
    expect(readActiveConnection()).toBeNull();
    clearActiveConnection();
    expect(session().getItem(ACTIVE_CONNECTION_KEY)).toBeNull();
  });
});
