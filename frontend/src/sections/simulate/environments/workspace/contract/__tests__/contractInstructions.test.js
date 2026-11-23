import { describe, it, expect } from "vitest";
import { contractInstruction } from "../contractInstructions";

describe("contractInstruction", () => {
  it("asks the builder to change a rule by its position", () => {
    expect(
      contractInstruction({
        target: "rule",
        op: "edit",
        index: 2,
        text: "Never refund twice.",
        next: "Never refund an order twice.",
      }),
    ).toBe(
      'Change rule 3 of the contract from "Never refund twice." to "Never refund an order twice.". Update the sub-goals and checks that depend on it, and keep everything else as it is.',
    );
  });

  it("asks the builder to add a rule", () => {
    expect(
      contractInstruction({ target: "rule", op: "add", next: "Verify first." }),
    ).toBe(
      'Add this rule to the contract: "Verify first.". Add the sub-goals and checks it needs, and keep everything else as it is.',
    );
  });

  it("asks the builder to remove a rule, quoting it", () => {
    expect(
      contractInstruction({
        target: "rule",
        op: "remove",
        index: 0,
        text: "Verify first.",
      }),
    ).toBe(
      'Remove rule 1 ("Verify first.") from the contract. Remove or update the sub-goals and checks that depend on it, and keep everything else as it is.',
    );
  });

  it("words use case changes around the scenarios that depend on them", () => {
    expect(
      contractInstruction({
        target: "useCase",
        op: "edit",
        index: 1,
        text: "Refund a late order",
        next: "Refund a damaged order",
      }),
    ).toBe(
      'Change use case 2 of the contract from "Refund a late order" to "Refund a damaged order". Update the scenarios and checks that depend on it, and keep everything else as it is.',
    );
    expect(
      contractInstruction({
        target: "useCase",
        op: "add",
        next: "Cancel a booking",
      }),
    ).toBe(
      'Add this use case to the contract: "Cancel a booking". Add the scenarios and checks it needs, and keep everything else as it is.',
    );
    expect(
      contractInstruction({
        target: "useCase",
        op: "remove",
        index: 0,
        text: "Cancel a booking",
      }),
    ).toBe(
      'Remove use case 1 ("Cancel a booking") from the contract. Remove or update the scenarios and checks that depend on it, and keep everything else as it is.',
    );
  });

  it("never writes an em dash", () => {
    ["add", "edit", "remove"].forEach((op) =>
      expect(
        contractInstruction({
          target: "rule",
          op,
          index: 0,
          text: "a",
          next: "b",
        }),
      ).not.toMatch(/\u2014/),
    );
  });
});
