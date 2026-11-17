// A model id that two or more of the org's providers list: the console says
// which provider a plain id reaches and how to pick one. Every claim here is
// one the gateway's org-routing tests prove (agentcc-gateway
// internal/server/org_route_order_test.go and org_route_prefix_test.go):
// a plain id reaches the provider whose id sorts first in byte order; an org
// model map entry naming a provider that lists it pins it there; and
// "<provider>/<model>" reaches the provider it names.
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import DuplicateModelNotice, {
  byteOrder,
  duplicateModelIds,
} from "./duplicateModels";

const providers = [
  { id: "openai", models: ["gpt-4o", "m", "only-here"] },
  { id: "azure-openai", models: ["gpt-4o"] },
  { id: "Zulu", models: ["m"] },
  { id: "vllm", models: ["m"] },
];

describe("which model ids more than one provider lists", () => {
  it("sorts provider ids in byte order, upper case first", () => {
    expect(["openai", "Zulu", "azure-openai"].sort(byteOrder)).toEqual([
      "Zulu",
      "azure-openai",
      "openai",
    ]);
  });

  it("flags each id two or more providers list, and the provider a plain id reaches", () => {
    expect(duplicateModelIds(providers)).toEqual([
      {
        model: "gpt-4o",
        providerIds: ["azure-openai", "openai"],
        reaches: "azure-openai",
        pinnedBy: "order",
        mapEntry: null,
      },
      {
        model: "m",
        providerIds: ["Zulu", "openai", "vllm"],
        reaches: "Zulu",
        pinnedBy: "order",
        mapEntry: null,
      },
    ]);
  });

  it("leaves out a provider the gateway does not have (not pushed)", () => {
    const out = duplicateModelIds([
      { id: "a", models: ["x"] },
      { id: "b", models: ["x"], pushed: false },
    ]);
    expect(out).toEqual([]);
  });

  it("counts a provider once even if it lists the id twice", () => {
    expect(
      duplicateModelIds([
        { id: "a", models: ["x", "x"] },
        { id: "b", models: [] },
      ]),
    ).toEqual([]);
  });

  it("follows a model map entry that names a provider listing the id", () => {
    const [entry] = duplicateModelIds(providers.slice(0, 2), {
      "gpt-4o": "openai",
    });
    expect(entry).toMatchObject({
      model: "gpt-4o",
      reaches: "openai",
      pinnedBy: "model_map",
      mapEntry: "openai",
    });
  });

  // A map entry naming a provider that does not list the id is not one the
  // tests cover: the console states the entry and claims no route.
  it("claims no route for a model map entry naming a provider that does not list the id", () => {
    const [entry] = duplicateModelIds(providers.slice(0, 2), {
      "gpt-4o": "elsewhere",
    });
    expect(entry).toMatchObject({
      reaches: null,
      pinnedBy: "model_map_other",
      mapEntry: "elsewhere",
    });
  });
});

const SORTED =
  'A plain "gpt-4o" reaches azure-openai, the provider whose id sorts first.';
const HOW =
  'To pick a provider, send "<provider>/<model>" (for example "openai/gpt-4o"), or add an entry under Gateway settings › Org config › Model Map.';

describe("the notice", () => {
  it("says who lists each id, where a plain id goes, and how to pick", () => {
    render(<DuplicateModelNotice providers={providers} />);
    const notice = screen.getByTestId("duplicate-model-notice");
    expect(notice).toHaveTextContent("Models listed by more than one provider");
    expect(notice).toHaveTextContent(
      '"gpt-4o" is listed by azure-openai and openai.',
    );
    expect(notice).toHaveTextContent(SORTED);
    expect(notice).toHaveTextContent('"m" is listed by Zulu, openai and vllm.');
    expect(notice).toHaveTextContent(
      'A plain "m" reaches Zulu, the provider whose id sorts first.',
    );
    expect(notice).toHaveTextContent(HOW);
    expect(notice).not.toHaveTextContent("only-here");
  });

  it("says the model map entry decides it when it names a provider that lists the id", () => {
    render(
      <DuplicateModelNotice
        providers={providers.slice(0, 2)}
        modelMap={{ "gpt-4o": "openai" }}
      />,
    );
    expect(screen.getByTestId("duplicate-model-notice")).toHaveTextContent(
      'Your Model Map entry sends a plain "gpt-4o" to openai.',
    );
    expect(screen.queryByText(SORTED)).toBeNull();
  });

  it("states a model map entry naming another provider without claiming a route", () => {
    render(
      <DuplicateModelNotice
        providers={providers.slice(0, 2)}
        modelMap={{ "gpt-4o": "elsewhere" }}
      />,
    );
    const notice = screen.getByTestId("duplicate-model-notice");
    expect(notice).toHaveTextContent(
      'Your Model Map has an entry for "gpt-4o" naming elsewhere.',
    );
    expect(notice).not.toHaveTextContent(/A plain "gpt-4o" reaches/);
  });

  it("shows nothing when no id is listed twice", () => {
    const { container } = render(
      <DuplicateModelNotice providers={[{ id: "a", models: ["x"] }]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
