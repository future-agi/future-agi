/* eslint-env node */
// Small helpers shared by the embed-host e2e specs.
const crypto = require("node:crypto");
const { expect } = require("playwright/test");

const { CHILD, CONTROL, PARENT, THIRD } = require("./ports.cjs");

const newToken = () => crypto.randomBytes(32).toString("base64url");
const sha256 = (text) => crypto.createHash("sha256").update(text).digest("hex");
const link = (origin, connect, token, path = "/dashboard/studio/runs/1") =>
  `${origin}${path}#connect=${connect}&t=${token}`;

const frameAt = (page, prefix) =>
  page.frames().find((frame) => frame.url().startsWith(prefix));
const waitForFrame = async (page, prefix) => {
  await expect.poll(() => Boolean(frameAt(page, prefix))).toBe(true);
  return frameAt(page, prefix);
};

const surface = (page) => page.getByTestId("omega-embedded-surface");
const gate = (page) => page.getByTestId("omega-embed-gate");

module.exports = {
  CHILD,
  CONTROL,
  PARENT,
  THIRD,
  frameAt,
  gate,
  link,
  newToken,
  sha256,
  surface,
  waitForFrame,
};
