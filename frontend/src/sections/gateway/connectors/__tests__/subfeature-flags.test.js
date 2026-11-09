import { describe, expect, it } from "vitest";
import { connectableApps } from "../catalog-model";

const key = { id: "key", credential: { mode: "shared", kind: "bearer" } };
const oauth = {
  id: "oauth",
  credential: { mode: "per_user", kind: "oauth" },
  connect: { served: true },
};
const apps = [
  { id: "static", providers: [key], servers: [] },
  {
    id: "signin",
    providers: [oauth],
    servers: [{ credential_mode: "per_user" }],
  },
  { id: "gmail", group: "google", providers: [oauth], servers: [] },
];

describe("deployment sub-feature visibility", () => {
  it("defaults off, including previously connected OAuth apps", () => {
    expect(connectableApps(apps).map((app) => app.id)).toEqual(["static"]);
  });
  it("upstream OAuth does not enable K2", () => {
    expect(
      connectableApps(apps, { upstreamOAuth: true }).map((app) => app.id),
    ).toEqual(["static", "signin"]);
  });
  it("K2 does not enable upstream OAuth", () => {
    expect(
      connectableApps(apps, { k2Apps: true }).map((app) => app.id),
    ).toEqual(["static"]);
  });
  it("both on preserve sign-in apps and static credentials", () => {
    expect(
      connectableApps(apps, { upstreamOAuth: true, k2Apps: true }).map(
        (app) => app.id,
      ),
    ).toEqual(["static", "signin", "gmail"]);
  });
});
