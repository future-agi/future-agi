import { describe, expect, it } from "vitest";
import {
  PROMPT_MAX,
  changeBody,
  initialValues,
  nameProblem,
  phoneProblem,
  promptProblem,
} from "../syncAgentValues";

const START = {
  prompt: "You answer refill calls.",
  phoneNumber: "+14155550100",
  agentName: "front-desk",
  speaksFirst: false,
};

describe("promptProblem", () => {
  it("asks for a prompt when it is empty", () => {
    expect(promptProblem("")).toBe("Enter the prompt your agent runs with");
    expect(promptProblem("   ")).toBe("Enter the prompt your agent runs with");
  });

  it("refuses a prompt over the limit", () => {
    expect(promptProblem("a".repeat(PROMPT_MAX + 1))).toBe(
      `Keep it under ${PROMPT_MAX.toLocaleString()} characters`,
    );
  });

  it("takes a prompt within the limit", () => {
    expect(promptProblem("a".repeat(PROMPT_MAX))).toBeNull();
    expect(promptProblem("You answer refill calls.")).toBeNull();
  });
});

describe("phoneProblem", () => {
  it("asks for a number when it is empty", () => {
    expect(phoneProblem("")).toBe("Enter the number the platform calls");
    expect(phoneProblem("  ")).toBe("Enter the number the platform calls");
  });

  it("wants the international format", () => {
    expect(phoneProblem("4155550100")).toBe(
      "In international format, like +14155550100.",
    );
  });

  it("refuses a number with too few digits", () => {
    expect(phoneProblem("+1415")).toBe("Enter exactly 10 digits after +1");
  });

  it("takes a valid number", () => {
    expect(phoneProblem("+14155550100")).toBeNull();
    expect(phoneProblem(" +14155550100 ")).toBeNull();
  });
});

describe("nameProblem", () => {
  it("asks for a name when it is empty", () => {
    expect(nameProblem("  ")).toBe(
      "Enter the name your LiveKit worker registers with",
    );
  });

  it("refuses a name over 512 characters", () => {
    expect(nameProblem("a".repeat(513))).toBe("Keep it under 512 characters");
  });

  it("takes a valid name", () => {
    expect(nameProblem("a".repeat(512))).toBeNull();
    expect(nameProblem("front-desk")).toBeNull();
  });
});

describe("initialValues", () => {
  it("reads the version's config snapshot", () => {
    expect(
      initialValues({
        description: "You answer refill calls.",
        contact_number: "+14155550100",
        livekit_agent_name: "front-desk",
        target_speaks_first: true,
      }),
    ).toEqual({
      prompt: "You answer refill calls.",
      phoneNumber: "+14155550100",
      agentName: "front-desk",
      speaksFirst: true,
    });
  });

  it("starts empty when the snapshot has nothing", () => {
    expect(initialValues()).toEqual({
      prompt: "",
      phoneNumber: "",
      agentName: "",
      speaksFirst: false,
    });
    expect(initialValues({ target_speaks_first: "yes" }).speaksFirst).toBe(
      false,
    );
  });
});

describe("changeBody", () => {
  it("sends no prompt or settings when nothing moved", () => {
    expect(changeBody("phone", START, { ...START })).toEqual({
      kind: "manual",
    });
    expect(
      changeBody("livekit", START, {
        ...START,
        prompt: `  ${START.prompt}  `,
        agentName: ` ${START.agentName} `,
      }),
    ).toEqual({ kind: "manual" });
  });

  it("sends the trimmed phone number when it changed", () => {
    expect(
      changeBody("phone", START, { ...START, phoneNumber: " +14155550199 " }),
    ).toEqual({ kind: "manual", settings: { phone_number: "+14155550199" } });
  });

  it("sends the LiveKit agent name when it changed", () => {
    expect(
      changeBody("livekit", START, { ...START, agentName: " returns-line " }),
    ).toEqual({ kind: "manual", settings: { agent_name: "returns-line" } });
  });

  it("never sends an agent name for a phone agent", () => {
    const body = changeBody("phone", START, {
      ...START,
      agentName: "returns-line",
    });
    expect(body).toEqual({ kind: "manual" });
  });

  it("never sends a phone number for a LiveKit agent", () => {
    const body = changeBody("livekit", START, {
      ...START,
      phoneNumber: "+14155550199",
    });
    expect(body).toEqual({ kind: "manual" });
  });

  it("sends who speaks first when it was toggled", () => {
    expect(changeBody("phone", START, { ...START, speaksFirst: true })).toEqual(
      { kind: "manual", settings: { target_speaks_first: true } },
    );
  });

  it("sends the trimmed prompt when it changed", () => {
    expect(
      changeBody("livekit", START, {
        ...START,
        prompt: "  You handle returns.  ",
      }),
    ).toEqual({ kind: "manual", prompt: "You handle returns." });
  });
});
