// integration review must-fix 2: as of an older version a field's ⋯ has no
// `Comment on this field`; when the field has nothing to translate either,
// there is no ⋯ at all (never an empty menu). The Translate deep link still
// says why there is nothing to translate.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import FieldCollabMenu from "../FieldCollabMenu";

const info = { current: null };
vi.mock("src/api/develop/row-collab", () => ({
  collabError: (e, fallback) => fallback,
  useTranslate: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useTranslateInfo: () => ({ data: info.current }),
}));
const notified = [];
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (text) => notified.push(text),
}));

const NO_TARGETS = {
  languages: [{ code: "en", name: "English" }],
  fields: [
    {
      column_id: "c1",
      name: "customer_message",
      language: "en",
      language_name: "English",
      available: [],
    },
  ],
};
const WITH_TARGETS = {
  ...NO_TARGETS,
  languages: [
    { code: "en", name: "English" },
    { code: "es", name: "Spanish" },
  ],
  fields: [{ ...NO_TARGETS.fields[0], available: ["es"] }],
};

const mount = (props, url = "/") =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <FieldCollabMenu
        datasetId="GS"
        rowId="r1"
        columnId="c1"
        name="customer_message"
        rowLabel="Row 1"
        onTranslated={() => {}}
        {...props}
      />
    </MemoryRouter>,
  );

describe("a field's ⋯ menu", () => {
  afterEach(() => {
    cleanup();
    notified.length = 0;
  });

  it("as of a version with nothing to translate: no ⋯ at all", () => {
    info.current = NO_TARGETS;
    mount({ canComment: false });
    expect(screen.queryByTestId("field-more-customer_message")).toBeNull();
  });

  it("before the translate read answers, as of a version: no ⋯ either", () => {
    info.current = undefined;
    mount({ canComment: false });
    expect(screen.queryByTestId("field-more-customer_message")).toBeNull();
  });

  it("as of a version with a translation: the ⋯ offers Translate only", async () => {
    info.current = WITH_TARGETS;
    mount({ canComment: false });
    fireEvent.click(screen.getByTestId("field-more-customer_message"));
    expect(await screen.findByTestId("field-translate")).toBeTruthy();
    expect(screen.queryByTestId("field-comment")).toBeNull();
  });

  it("on the latest version the ⋯ keeps Comment on this field", async () => {
    info.current = NO_TARGETS;
    mount({ canComment: true });
    fireEvent.click(screen.getByTestId("field-more-customer_message"));
    expect(await screen.findByTestId("field-comment")).toBeTruthy();
  });

  it("the Translate deep link still says why there is nothing to translate", () => {
    info.current = NO_TARGETS;
    mount({ canComment: false, autoOpen: true });
    expect(screen.queryByTestId("field-more-customer_message")).toBeNull();
    expect(notified).toHaveLength(1);
  });
});
