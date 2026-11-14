import { describe, it, expect } from "vitest";
import {
  isValidNumericInput,
  isCompleteNumericValue,
} from "../TraceFilterPanel";

describe("isValidNumericInput (TH-5195)", () => {
  it("accepts empty / null / undefined as valid (typing-intermediate)", () => {
    expect(isValidNumericInput("")).toBe(true);
    expect(isValidNumericInput(null)).toBe(true);
    expect(isValidNumericInput(undefined)).toBe(true);
  });

  it("accepts integers, decimals, and negatives", () => {
    expect(isValidNumericInput("0")).toBe(true);
    expect(isValidNumericInput("42")).toBe(true);
    expect(isValidNumericInput("1.5")).toBe(true);
    expect(isValidNumericInput("-7")).toBe(true);
    expect(isValidNumericInput("-3.14")).toBe(true);
  });

  it("accepts partial typing states so the user isn't blocked mid-input", () => {
    expect(isValidNumericInput("-")).toBe(true);
    expect(isValidNumericInput(".")).toBe(true);
    expect(isValidNumericInput("-.")).toBe(true);
    expect(isValidNumericInput("1.")).toBe(true);
  });

  it("rejects letters, mixed input, and malformed numbers", () => {
    expect(isValidNumericInput("abc")).toBe(false);
    expect(isValidNumericInput("1.5abc")).toBe(false);
    expect(isValidNumericInput("1.5.6")).toBe(false);
    expect(isValidNumericInput("--1")).toBe(false);
  });

  it("trims surrounding whitespace so the error state matches the Apply gate", () => {
    expect(isValidNumericInput(" 1")).toBe(true);
    expect(isValidNumericInput(" 1.5 ")).toBe(true);
    expect(isValidNumericInput(" ")).toBe(true);
    expect(isValidNumericInput(" 1.5abc ")).toBe(false);
  });

  it("rejects scientific notation (backend filter parser doesn't accept it)", () => {
    expect(isValidNumericInput("1e3")).toBe(false);
    expect(isValidNumericInput("2E-2")).toBe(false);
  });
});

describe("isCompleteNumericValue (TH-5195)", () => {
  it("treats empty / null / undefined as valid because handleApply drops empty rows", () => {
    expect(isCompleteNumericValue("")).toBe(true);
    expect(isCompleteNumericValue(null)).toBe(true);
    expect(isCompleteNumericValue(undefined)).toBe(true);
  });

  it("accepts complete integers, decimals, and negatives", () => {
    expect(isCompleteNumericValue("0")).toBe(true);
    expect(isCompleteNumericValue("42")).toBe(true);
    expect(isCompleteNumericValue("1.5")).toBe(true);
    expect(isCompleteNumericValue(".5")).toBe(true);
    expect(isCompleteNumericValue("-7")).toBe(true);
    expect(isCompleteNumericValue("-.5")).toBe(true);
  });

  it("rejects typing-intermediate states that would NaN at apply", () => {
    expect(isCompleteNumericValue("-")).toBe(false);
    expect(isCompleteNumericValue(".")).toBe(false);
    expect(isCompleteNumericValue("-.")).toBe(false);
  });

  it("rejects letters, mixed input, and malformed numbers", () => {
    expect(isCompleteNumericValue("abc")).toBe(false);
    expect(isCompleteNumericValue("1.5abc")).toBe(false);
    expect(isCompleteNumericValue("1.5.6")).toBe(false);
    expect(isCompleteNumericValue("--1")).toBe(false);
  });

  it("rejects scientific notation (matches isValidNumericInput contract)", () => {
    expect(isCompleteNumericValue("1e3")).toBe(false);
    expect(isCompleteNumericValue("2E-2")).toBe(false);
  });

  it("trims surrounding whitespace before validating", () => {
    expect(isCompleteNumericValue(" 1.5 ")).toBe(true);
    expect(isCompleteNumericValue("  -3 ")).toBe(true);
  });

  it("treats whitespace-only like empty (agrees with isValidNumericInput)", () => {
    expect(isCompleteNumericValue(" ")).toBe(true);
  });
});
