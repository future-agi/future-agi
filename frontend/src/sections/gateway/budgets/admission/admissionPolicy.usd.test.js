import { describe, expect, it } from "vitest";
import * as policy from "./admissionPolicy";

function fn(name) {
  expect(policy[name], `${name} must be exported`).toBeTypeOf("function");
  return policy[name];
}

describe("USD amount contract: plain decimal, at most six decimal places", () => {
  it("accepts whole micro-dollar amounts and reports their exact micros", () => {
    const parse = fn("parseUsd");
    expect(parse("5")).toEqual({ value: 5, micros: 5_000_000 });
    expect(parse(" 7 ")).toEqual({ value: 7, micros: 7_000_000 });
    expect(parse("5.")).toEqual({ value: 5, micros: 5_000_000 });
    expect(parse(".5")).toEqual({ value: 0.5, micros: 500_000 });
    expect(parse("0")).toEqual({ value: 0, micros: 0 });
    expect(parse("0.25")).toEqual({ value: 0.25, micros: 250_000 });
    expect(parse("0.000001")).toEqual({ value: 0.000001, micros: 1 });
    expect(parse("1000000000")).toEqual({
      value: 1_000_000_000,
      micros: 1_000_000_000_000_000,
    });
  });
  it("rejects more than six decimals instead of rounding, and says why", () => {
    const parse = fn("parseUsd");
    for (const text of ["0.0000009", "1.0000001", "0.1234567", "5.0000000"]) {
      expect(() => parse(text), text).toThrow(/six decimal places/i);
      expect(() => parse(text), text).toThrow(/micro-dollar/i);
    }
  });
  it("rejects blank, signed, exponent, hex, grouped and non-finite text", () => {
    const parse = fn("parseUsd");
    for (const text of [
      "",
      "  ",
      "NaN",
      "Infinity",
      "-1",
      "+1",
      "1e3",
      "1e-7",
      "0x10",
      "1,5",
      "abc",
      ".",
    ])
      expect(() => parse(text), JSON.stringify(text)).toThrow();
    expect(() => parse("")).toThrow(/0 is a hard zero-dollar cap/);
  });
  it("rejects amounts above the contract maximum", () => {
    const parse = fn("parseUsd");
    expect(() => parse("1000000001")).toThrow(/1,000,000,000/);
    expect(() => parse("1000000000.000001")).toThrow(/1,000,000,000/);
  });
});

describe("effective micro-dollar threshold (gateway capMicros floor)", () => {
  it("floors limit x 1,000,000 exactly as the gateway does", () => {
    const micros = fn("effectiveMicros");
    expect(micros(5)).toBe(5_000_000);
    expect(micros(0.57)).toBe(570_000);
    expect(micros(0)).toBe(0);
    // Sub-micro: configured positive, enforced as zero.
    expect(micros(0.0000009)).toBe(0);
    // A six-decimal amount whose binary float floors one micro-dollar low.
    expect(micros(0.000249)).toBe(248);
    expect(micros(0.000251)).toBe(250);
  });
  it("returns null for amounts the gateway would refuse as invalid", () => {
    const micros = fn("effectiveMicros");
    for (const value of [
      NaN,
      Infinity,
      -1,
      1_000_000_001,
      "x",
      null,
      undefined,
    ])
      expect(micros(value)).toBeNull();
  });
  it("formats micros and describes configured versus enforced amounts", () => {
    const format = fn("formatMicros");
    expect(format(0)).toBe("$0");
    expect(format(5_000_000)).toBe("$5");
    expect(format(1_500_000)).toBe("$1.5");
    expect(format(248)).toBe("$0.000248");
    expect(format(1_234_567)).toBe("$1.234567");
    const view = fn("amountView");
    expect(view(5)).toMatchObject({
      configured: "$5",
      enforced: "$5",
      micros: 5_000_000,
      exact: true,
    });
    expect(view(0.0000009)).toMatchObject({
      configured: "$0.0000009",
      enforced: "$0",
      micros: 0,
      exact: false,
    });
    expect(view(0.000249)).toMatchObject({
      enforced: "$0.000248",
      micros: 248,
      exact: false,
    });
    expect(view("junk")).toMatchObject({ micros: null, exact: false });
  });
  it("renders stored numbers as plain decimals, never exponent text", () => {
    const text = fn("usdText");
    expect(text(5)).toBe("5");
    expect(text(0.1)).toBe("0.1");
    expect(text(1e-7)).toBe("0.0000001");
    expect(text(0.0000009)).toBe("0.0000009");
    expect(text(undefined)).toBe("");
    expect(text(null)).toBe("");
  });
  it("states the contract the form enforces", () => {
    expect(policy.AMOUNT_CONTRACT).toMatch(/six decimal places/i);
    expect(policy.AMOUNT_CONTRACT).toMatch(/micro-dollar/i);
    expect(policy.AMOUNT_CONTRACT).toMatch(/rounds? .*down|floor/i);
  });
});
