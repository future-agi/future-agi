/* eslint-disable react/prop-types */
import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import EditImages from "./EditImages";

const dndState = vi.hoisted(() => ({ onDragEnd: null }));

vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children, onDragEnd }) => {
    dndState.onDragEnd = onDragEnd;
    return <div>{children}</div>;
  },
  PointerSensor: vi.fn(),
  closestCenter: vi.fn(),
  useSensor: vi.fn(),
  useSensors: vi.fn(() => []),
}));

vi.mock("@dnd-kit/sortable", () => ({
  SortableContext: ({ children }) => <div>{children}</div>,
  arrayMove: (items, from, to) => {
    const result = [...items];
    const [item] = result.splice(from, 1);
    result.splice(to, 0, item);
    return result;
  },
  rectSortingStrategy: vi.fn(),
  useSortable: ({ id }) => ({
    attributes: {},
    listeners: {},
    setNodeRef: (node) => {
      if (node) node.dataset.sortableId = id;
    },
    transform: null,
    transition: null,
    isDragging: false,
  }),
}));

vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: () => null } },
}));

vi.mock("react-dropzone", () => ({
  useDropzone: () => ({
    getInputProps: () => ({}),
    getRootProps: () => ({}),
    isDragActive: false,
  }),
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/components/gridIcon/GridIcon", () => ({
  default: ({ alt, src }) => (
    <img data-testid="image-thumbnail" src={src} alt={alt} />
  ),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span />,
}));

vi.mock("src/components/show", () => ({
  ShowComponent: ({ children, condition }) =>
    condition ? <>{children}</> : null,
}));

vi.mock("./ConfirmDelete", () => ({
  default: ({ onDelete, open }) =>
    open ? (
      <button data-testid="confirm-delete" onClick={onDelete}>
        Confirm
      </button>
    ) : null,
}));

vi.mock("./ErrorMessage", () => ({
  default: () => null,
}));

describe("EditImages", () => {
  it("keeps sortable identity attached to each image after reordering duplicate URLs", async () => {
    const onCellValueChanged = vi.fn();

    render(
      <EditImages
        params={{
          value: JSON.stringify([
            "duplicate-url",
            "duplicate-url",
            "third-url",
          ]),
        }}
        onClose={vi.fn()}
        onCellValueChanged={onCellValueChanged}
      />,
    );

    await waitFor(() =>
      expect(screen.getAllByTestId("image-thumbnail")).toHaveLength(3),
    );

    const initialItems = screen.getAllByTestId("image-thumbnail");
    expect(
      initialItems.map((item) => item.parentElement.dataset.sortableId),
    ).toEqual(["image-0", "image-1", "image-2"]);

    act(() => {
      dndState.onDragEnd({
        active: { id: "image-0" },
        over: { id: "image-2" },
      });
    });

    await waitFor(() => {
      expect(
        screen
          .getAllByTestId("image-thumbnail")
          .map((item) => item.parentElement.dataset.sortableId),
      ).toEqual(["image-1", "image-2", "image-0"]);
    });

    fireEvent.click(
      screen
        .getAllByTestId("image-thumbnail")[0]
        .parentElement.querySelector(".delete-btn"),
    );
    fireEvent.click(screen.getByTestId("confirm-delete"));

    await waitFor(() => {
      expect(
        screen
          .getAllByTestId("image-thumbnail")
          .map((item) => item.parentElement.dataset.sortableId),
      ).toEqual(["image-2", "image-0"]);
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onCellValueChanged).toHaveBeenCalledWith(
      expect.objectContaining({
        newValue: JSON.stringify(["third-url", "duplicate-url"]),
      }),
    );
  });
});
