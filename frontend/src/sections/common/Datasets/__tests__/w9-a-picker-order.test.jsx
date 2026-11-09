// @vitest-environment jsdom
// W9 A4, A6 (W9-SCOPE §5): pickers keep the server order (starred first)
// and show folder headers once any dataset is in a folder; the option label
// shows a star and the folder path only when the dataset carries them.
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import DatasetOptionLabel from "../DatasetOptionLabel";
import {
  datasetOptionText,
  folderGroups,
  folderHeader,
  hasFolders,
  pickerCaption,
  pickerGroups,
  pickerOrder,
  pickerRows,
  STARRED_HEADER,
} from "../pickerOrder";

const D = (name, extra = {}) => ({ datasetId: name, name, ...extra });

describe("picker order", () => {
  it("keeps the server order when no dataset is in a folder", () => {
    const list = [D("b", { starred: true }), D("a"), D("c")];
    expect(hasFolders(list)).toBe(false);
    expect(pickerOrder(list).map((d) => d.name)).toEqual(["b", "a", "c"]);
    expect(pickerRows(list)).toEqual(
      list.map((dataset, index) => ({ dataset, index })),
    );
  });

  it("groups by folder: starred first, folders in path order, No folder last", () => {
    const list = [
      D("starred-in-support", { starred: true, folder: "support" }),
      D("loose"),
      D("billing", { folder: "/support/billing/" }),
      D("support-2", { folder: "support" }),
    ];
    expect(
      folderGroups(list).map((g) => [g.folder, g.items.map((d) => d.name)]),
    ).toEqual([
      ["support", ["starred-in-support", "support-2"]],
      ["", ["loose"]],
      ["support/billing", ["billing"]],
    ]);
    // Starred datasets lead (their own group, folder path in the label);
    // the rest sit under folder headers in order of first appearance.
    expect(pickerOrder(list).map((d) => d.name)).toEqual([
      "starred-in-support",
      "support-2",
      "billing",
      "loose",
    ]);
    const rows = pickerRows(list);
    expect(
      rows.map((r) =>
        r.header !== undefined
          ? `#${r.header}`
          : `${r.index}:${r.dataset.name}${r.inStarred ? "*" : ""}`,
      ),
    ).toEqual([
      "#Starred",
      "0:starred-in-support*",
      "#support",
      "1:support-2",
      "#support/billing",
      "2:billing",
      "#No folder",
      "3:loose",
    ]);
    expect(folderHeader("")).toBe("No folder");
    expect(folderHeader("support/billing")).toBe("support/billing");
  });

  it("starred datasets lead even when their folders come later", () => {
    const list = [
      D("star-new", { starred: true }),
      D("plain-a"),
      D("star-old", { starred: true, folder: "support" }),
      D("in-support", { folder: "support" }),
    ];
    expect(pickerOrder(list).map((d) => d.name)).toEqual([
      "star-new",
      "star-old",
      "in-support",
      "plain-a",
    ]);
    expect(pickerGroups(list).map((g) => g.header)).toEqual([
      STARRED_HEADER,
      "support",
      "No folder",
    ]);
    // Without folders: no headers, the server order (starred first) as is.
    expect(pickerGroups([D("a", { starred: true }), D("b")])).toEqual([
      {
        key: "all",
        header: null,
        items: [D("a", { starred: true }), D("b")],
      },
    ]);
  });

  it("says how the list is ordered", () => {
    expect(pickerCaption([D("a"), D("b")])).toBe("Recent first");
    expect(pickerCaption([D("a", { starred: true }), D("b")])).toBe(
      "Starred first, then recent",
    );
    expect(pickerCaption([D("a", { folder: "x" }), D("b")])).toBe(
      "By folder, recent first",
    );
    expect(
      pickerCaption([D("a", { starred: true }), D("b", { folder: "x" })]),
    ).toBe("Starred first, then by folder");
  });

  it("names an option in words", () => {
    expect(
      datasetOptionText(D("GS", { starred: true, folder: "support" })),
    ).toBe("Starred · support · GS");
    expect(datasetOptionText(D("GS"))).toBe("GS");
  });
});

describe("DatasetOptionLabel", () => {
  it("shows the star and the folder only when present", () => {
    const { rerender } = render(
      <DatasetOptionLabel
        dataset={D("Golden", { starred: true, folder: "support/billing" })}
        suffix=" · 248 rows"
      />,
    );
    expect(screen.getByTestId("dataset-option-star")).toBeTruthy();
    expect(screen.getByTestId("dataset-option-folder").textContent).toBe(
      "support/billing /",
    );
    expect(screen.getByText(/Golden/).textContent).toBe("Golden · 248 rows");
    rerender(<DatasetOptionLabel dataset={D("Plain")} suffix=" · 3 rows" />);
    expect(screen.queryByTestId("dataset-option-star")).toBeNull();
    expect(screen.queryByTestId("dataset-option-folder")).toBeNull();
    expect(screen.getByText(/Plain/).textContent).toBe("Plain · 3 rows");
  });

  it("hides the folder under a folder header", () => {
    render(
      <DatasetOptionLabel
        dataset={D("Golden", { folder: "support" })}
        showFolder={false}
      />,
    );
    expect(screen.queryByTestId("dataset-option-folder")).toBeNull();
  });
});
