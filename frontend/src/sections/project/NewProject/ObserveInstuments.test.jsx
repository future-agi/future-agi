/* eslint-disable react/prop-types */
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import ObserveInstruments from "./ObserveInstuments";

// Keep the real module, but render the sample as a plain block that carries
// its text and language, so a test can read which code a card offers.
vi.mock("./InstructionCodeCopy", async (importOriginal) => ({
  ...(await importOriginal()),
  default: ({ text, language }) => <pre data-language={language}>{text}</pre>,
}));

it("ignores remote telemetry-provider logos in realistic API rows", () => {
  render(
    <ObserveInstruments
      isSuccess
      languageTab="python"
      onLanguageChange={vi.fn()}
      data={{
        openai: {
          name: "OpenAI",
          logo: "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png",
          Python: { code: "pass" },
        },
        custom: {
          name: "Unknown provider",
          logo: "https://fi-content-dev.s3.ap-south-1.amazonaws.com/custom.png",
          Python: { code: "pass" },
        },
      }}
    />,
  );
  expect(screen.getByAltText("OpenAI Logo")).toHaveAttribute(
    "src",
    "/icons/settings/openai.png",
  );
  expect(screen.getByAltText("Unknown provider Logo")).toHaveAttribute(
    "src",
    "/assets/icons/settings/AIProviders.svg",
  );
  expect(document.querySelector('img[src^="http"], img[src^="//"]')).toBeNull();
});

// Shaped like the API's instruments rows: CrewAI has Python only, OpenAI has
// both, Vercel AI SDK has TypeScript only. CrewAI comes first, as in the API.
const instruments = {
  crewai: {
    name: "CrewAI",
    Python: {
      github:
        "https://github.com/future-agi/traceAI/tree/main/python/frameworks/crewai",
      code: "# pip install traceai-crewai\nfrom traceai_crewai import CrewAIInstrumentor",
    },
  },
  openai: {
    name: "OpenAI",
    Python: { code: "from traceai_openai import OpenAIInstrumentor" },
    TypeScript: {
      code: 'import { OpenAIInstrumentation } from "@traceai/openai";',
    },
  },
  vercel: {
    name: "Vercel AI SDK",
    TypeScript: {
      code: 'import { VercelAIInstrumentation } from "@traceai/vercel";',
    },
  },
};

const renderTab = (languageTab) =>
  render(
    <ObserveInstruments
      isSuccess
      languageTab={languageTab}
      onLanguageChange={vi.fn()}
      data={instruments}
    />,
  );

// Each instrumentor is one accordion card. Its details stay mounted while the
// card is collapsed, so any code the card offers is in the DOM.
const card = (name) => screen.getByText(name).closest(".MuiAccordion-root");
const cardNames = () =>
  screen
    .getAllByText(/^(CrewAI|OpenAI|Vercel AI SDK)$/)
    .map((el) => el.textContent);

describe("instrumentation cards by language", () => {
  it("on the TypeScript tab, a Python-only entry shows Python only and no Python code", () => {
    renderTab("typescript");
    const crew = card("CrewAI");
    expect(within(crew).getByText("Python only")).toBeInTheDocument();
    expect(
      within(crew).getByText(/Switch to Python to see its setup code/),
    ).toBeInTheDocument();
    // No fallback: no code block and no lone Python tab in the card.
    expect(crew.querySelector("pre")).toBeNull();
    expect(crew.querySelector('[role="tab"]')).toBeNull();
    expect(screen.queryByText(/traceai_crewai|pip install/)).toBeNull();
    // The source link stays reachable.
    expect(within(crew).getByAltText("github icon")).toBeInTheDocument();
  });

  it("on the TypeScript tab, entries with TypeScript come first and none are dropped", () => {
    renderTab("typescript");
    expect(cardNames()).toEqual(["OpenAI", "Vercel AI SDK", "CrewAI"]);
    expect(document.querySelectorAll(".MuiAccordion-root")).toHaveLength(3);
  });

  it("on the TypeScript tab, a TypeScript entry shows its TypeScript snippet", () => {
    renderTab("typescript");
    const openai = card("OpenAI");
    expect(within(openai).getByText(/@traceai\/openai/)).toHaveAttribute(
      "data-language",
      "typescript",
    );
    expect(within(openai).queryByText("Python only")).toBeNull();
    expect(screen.queryByText(/traceai_openai/)).toBeNull();
    expect(
      within(card("Vercel AI SDK")).getByText(/@traceai\/vercel/),
    ).toHaveAttribute("data-language", "typescript");
  });

  it("leaves the Python tab unchanged", () => {
    renderTab("python");
    expect(cardNames()).toEqual(["CrewAI", "OpenAI", "Vercel AI SDK"]);
    expect(screen.queryByText("Python only")).toBeNull();
    expect(screen.queryByText(/No TypeScript instrumentor/)).toBeNull();
    expect(within(card("CrewAI")).getByText(/traceai_crewai/)).toHaveAttribute(
      "data-language",
      "python",
    );
    expect(within(card("OpenAI")).getByText(/traceai_openai/)).toHaveAttribute(
      "data-language",
      "python",
    );
    // A TypeScript-only entry still shows its TypeScript code here, as before.
    expect(
      within(card("Vercel AI SDK")).getByText(/@traceai\/vercel/),
    ).toHaveAttribute("data-language", "typescript");
  });
});
