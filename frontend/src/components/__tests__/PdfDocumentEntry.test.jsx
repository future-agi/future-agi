import React from "react";
import PropTypes from "prop-types";
import { fireEvent, render } from "src/utils/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { route, openPreview } = vi.hoisted(() => ({
  route: {},
  openPreview: vi.fn(),
}));
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => route,
}));
vi.mock("src/utils/CommonStores/pdfPreviewStore", () => ({
  usePdfPreviewStoreShallow: (select) =>
    select({ setOpenPreviewPdfDrawer: openPreview }),
}));
vi.mock("src/components/tooltip", () => {
  const Tooltip = ({ children }) => <>{children}</>;
  Tooltip.propTypes = { children: PropTypes.node };
  return { default: Tooltip };
});
vi.mock("src/sections/common/DevelopCellRenderer/RenderMeta", () => ({
  default: () => null,
}));
vi.mock(
  "src/sections/develop-detail/DataTab/DoubleClickEditCell/ConfirmDelete",
  () => ({ default: () => null }),
);
vi.mock(
  "src/sections/develop-detail/DataTab/DoubleClickEditCell/ReplaceMediaDialog",
  () => ({ default: () => null }),
);

import FileCellRenderer from "src/sections/common/DevelopCellRenderer/CellRenderers/FileCellRenderer";

beforeEach(() => {
  delete route.dataset;
  delete route.experimentId;
  openPreview.mockClear();
});

for (const entry of ["dataset", "experimentId"]) {
  describe(`PDF document entry: ${entry}`, () => {
    it.each([
      ["snake_case", { document_url: "/stored.pdf" }, "/stored.pdf"],
      ["legacy camelCase", { documentUrl: "/legacy.pdf" }, "/legacy.pdf"],
      ["cell URL", {}, "/cell.pdf"],
    ])(
      "opens the actual document from %s metadata",
      (_name, valueInfos, expected) => {
        route[entry] = "synthetic-id";
        const { container } = render(
          <FileCellRenderer
            value="/cell.pdf"
            valueInfos={valueInfos}
            editable
            formattedValueReason={() => ""}
          />,
        );
        fireEvent.click(container.querySelector(".icon-actions button"));
        expect(openPreview).toHaveBeenCalledOnce();
        expect(openPreview).toHaveBeenCalledWith(
          expect.objectContaining({ url: expected, type: "pdf" }),
        );
      },
    );
  });
}
