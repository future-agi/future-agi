import { describe, it, expect } from "vitest";

import { phoneNumberError, isValidPhoneNumber } from "../phoneNumber";

const VALID = [
  "+14155551234",
  "+919123456789",
  "+8613812345678",
  "+5511987654321",
  "+5511912345678",
  "+4915123456789",
  "+6281234567890",
  "+447911123456",
  "+6581234567",
  "+3790123456",
  "+870123456789",
];

const INVALID = [
  ["+1415555123", "Enter exactly 10 digits after +1"],
  ["+141555512345", "Enter exactly 10 digits after +1"],
  ["+91912345678", "Enter exactly 10 digits after +91"],
  ["+9191234567890", "Enter exactly 10 digits after +91"],
  ["+4412", "Enter 7 to 15 digits including the country code"],
  ["+0123456789", "Enter a valid country code"],
  ["+1234567890123456", "Enter exactly 10 digits after +1"],
];

describe("phoneNumberError, typed in international form", () => {
  it.each(VALID)("accepts %s", (number) => {
    expect(phoneNumberError("+1", number)).toBeNull();
  });

  it.each(INVALID)("refuses %s", (number, message) => {
    expect(phoneNumberError("+1", number)).toBe(message);
  });
});

describe("phoneNumberError, national digits with the picked dial code", () => {
  it.each([
    ["+1", "4155551234"],
    ["+91", "9123456789"],
    ["+91", "09123456789"],
    ["+44", "07911123456"],
    ["+39", "0212345678"],
    ["+86", "13812345678"],
    ["+55", "11987654321"],
    ["+49", "15123456789"],
    ["+62", "81234567890"],
  ])("accepts %s %s", (dial, number) => {
    expect(phoneNumberError(dial, number)).toBeNull();
  });

  it.each([
    ["+1", "415555123", "Enter exactly 10 digits after +1"],
    ["+1", "41555512345", "Enter exactly 10 digits after +1"],
    ["+91", "91234567890", "Enter exactly 10 digits after +91"],
    ["+44", "1234", "Enter at least 5 digits"],
    ["+44", "12345678901234", "Enter at most 13 digits"],
  ])("refuses %s %s", (dial, number, message) => {
    expect(phoneNumberError(dial, number)).toBe(message);
  });

  it("treats an empty field as not yet valid, without an error", () => {
    expect(phoneNumberError("+1", "")).toBeNull();
    expect(isValidPhoneNumber("+1", "")).toBe(false);
  });
});
