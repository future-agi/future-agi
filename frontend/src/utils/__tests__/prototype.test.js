import { describe, expect, it } from "vitest";
import {
  NOT_IN_PROTOTYPE,
  isPrototypeMode,
  notInPrototypeProps,
  prototypeModeFromEnv,
  strictUnmockedFromEnv,
} from "../prototype";

describe("prototype mode flags", () => {
  it("is on only for local builds with the flag", () => {
    expect(
      prototypeModeFromEnv({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(true);
    expect(
      prototypeModeFromEnv({
        VITE_ENVIRONMENT: "production",
        VITE_PROTOTYPE_MODE: "true",
      }),
    ).toBe(false);
    expect(prototypeModeFromEnv({ VITE_ENVIRONMENT: "local" })).toBe(false);
    expect(prototypeModeFromEnv()).toBe(false);
  });

  it("strict unmocked mode needs prototype mode", () => {
    expect(
      strictUnmockedFromEnv({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_MODE: "true",
        VITE_PROTOTYPE_STRICT_UNMOCKED: "true",
      }),
    ).toBe(true);
    expect(
      strictUnmockedFromEnv({
        VITE_ENVIRONMENT: "local",
        VITE_PROTOTYPE_STRICT_UNMOCKED: "true",
      }),
    ).toBe(false);
  });

  it("isPrototypeMode reads the build env once", () => {
    expect(isPrototypeMode()).toBe(prototypeModeFromEnv(import.meta.env));
  });

  it("disables uncovered actions with the standard tooltip", () => {
    expect(notInPrototypeProps(true)).toEqual({
      disabled: true,
      title: NOT_IN_PROTOTYPE,
    });
    expect(notInPrototypeProps(false)).toEqual({});
    expect(NOT_IN_PROTOTYPE).toBe("Not in prototype");
  });
});
