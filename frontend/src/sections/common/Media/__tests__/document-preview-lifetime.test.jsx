/* eslint-disable react/prop-types */
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import DocChip from "../DocChip";
import DataTabLayout from "src/sections/develop-detail/DataTab/DataTabLayout";
import ResultsGrid from "src/sections/experiment-detail/results/ResultsGrid";

vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/utils/ag-grid-setup", () => ({}));
vi.mock("src/hooks/use-ag-theme", () => ({
  useAgThemeWith: () => undefined,
}));
vi.mock("src/sections/common/ux/oneview/useHoverSelection", () => ({
  default: () => ({ className: "" }),
}));
vi.mock("src/sections/common/ux/gridKeys", () => ({
  suppressGridTyping: () => false,
  useRowCursor: () => {},
}));
// Recreate only the cell, as AG Grid redrawRows / virtualization does. Keep
// the real grid host, DocChip and MUI dialog; no mock supplies preview state.
vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  const { default: DocumentChip } = await import("../DocChip");
  return {
    AgGridReact: forwardRef(function Grid({ rowData }, _ref) {
      return (
        <div role="grid">
          {rowData.map((row) => (
            <DocumentChip key={row.example_id} mediaRef={row.document} />
          ))}
        </div>
      );
    }),
  };
});

vi.mock(
  "src/sections/develop-detail/DataTab/Versions/VersionHistoryPanel",
  () => ({ default: ({ grid }) => grid }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DatapointDrawerV2/RowViewPanel",
  () => ({ default: ({ grid }) => grid }),
);
vi.mock("src/sections/develop-detail/DataTab/Versions/AsOfBanner", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/PendingChangesBar",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/Versions/RestoreVersionDialog",
  () => ({ default: () => null }),
);
vi.mock("src/sections/common/AddData/GenerateRowsDialog", () => ({
  default: () => null,
}));

afterEach(cleanup);

const PDF = {
  kind: "document",
  name: "fixture.pdf",
  url: "http://localhost/documents/fixture.pdf",
  mime: "application/pdf",
};
const OTHER = {
  kind: "document",
  name: "stored-document",
  url: "http://localhost/documents/stored-document",
  mime: "application/octet-stream",
};

function DatasetHost({ cellKey, document }) {
  return (
    <DataTabLayout>
      {document && <DocChip key={cellKey} mediaRef={document} />}
    </DataTabLayout>
  );
}

function ExperimentHost({ cellKey, document }) {
  return (
    <ResultsGrid
      rows={document ? [{ example_id: cellKey, document }] : []}
      columnDefs={[]}
      context={{}}
      rowHeight={60}
    />
  );
}

function Navigation({ navigation }) {
  navigation.current = useNavigate();
  return null;
}

function renderHost(Host, path, document) {
  const navigation = { current: null };
  const tree = (cellKey, item) => (
    <MemoryRouter initialEntries={[path]}>
      <Navigation navigation={navigation} />
      <Host cellKey={cellKey} document={item} />
    </MemoryRouter>
  );
  const view = render(tree("original-cell", document));
  return {
    ...view,
    redraw: (cellKey, item) => view.rerender(tree(cellKey, item)),
    navigate: (to) => act(() => navigation.current(to)),
  };
}

function openDocument(item) {
  fireEvent.click(screen.getByRole("button", { name: `Open ${item.name}` }));
}

function expectDocument(item) {
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  const dialog = screen.getByRole("dialog");
  const link = within(dialog).getByRole("link", { name: "Open in new tab" });
  expect(link.getAttribute("href")).toBe(item.url);
  expect(link.getAttribute("target")).toBe("_blank");
  const objects = dialog.querySelectorAll("object");
  expect(objects).toHaveLength(1);
  expect(objects[0].getAttribute("data")).toBe(item.url);
  expect(objects[0].getAttribute("aria-label")).toBe(item.name);
  expect(objects[0].getAttribute("type")).toBe(
    item.mime === "application/pdf" ? "application/pdf" : null,
  );
}

const expectClosed = () =>
  waitFor(() => expect(screen.queryAllByRole("dialog")).toHaveLength(0));

const HOSTS = [
  ["dataset", DatasetHost, "/dashboard/develop/dataset-a"],
  ["experiment", ExperimentHost, "/dashboard/develop/experiment/run-a/data"],
];

describe.each(HOSTS)("%s document preview", (_name, Host, path) => {
  it.each([PDF, OTHER])(
    "keeps $name open when its cell is replaced or removed",
    async (item) => {
      const next = item === PDF ? OTHER : PDF;
      const view = renderHost(Host, path, item);
      openDocument(item);
      expectDocument(item);

      // BASE loses both the local open state and the dialog here. The
      // replacement's value must not silently replace the open document.
      view.redraw("replacement-cell", next);
      expectDocument(item);
      view.redraw("removed-cell", null);
      expectDocument(item);
      view.redraw("returned-cell", next);
      expectDocument(item);

      fireEvent.click(screen.getByRole("button", { name: "Close" }));
      await expectClosed();
      openDocument(next);
      expectDocument(next);
      fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
      await expectClosed();
      view.redraw("another-cell", item);
      expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    },
  );

  it("keeps row navigation open but clears the preview on leaving the page", async () => {
    const view = renderHost(Host, path, PDF);
    openDocument(PDF);
    view.navigate(`${path}?row=other-row`);
    expectDocument(PDF);
    view.navigate("/dashboard/develop/other-dataset");
    await expectClosed();
    view.navigate(path);
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
    openDocument(PDF);
    expectDocument(PDF);
    view.unmount();
    expect(screen.queryAllByRole("dialog")).toHaveLength(0);
  });
});

describe("standalone document preview", () => {
  it("still opens and closes without a grid host", async () => {
    render(<DocChip mediaRef={PDF} />);
    openDocument(PDF);
    expectDocument(PDF);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await expectClosed();
  });
});
