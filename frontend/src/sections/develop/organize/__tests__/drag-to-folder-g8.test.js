// v2.1 g8 (org:drag-to-folder): the folder under the pointer is the drop
// target — pointerWithin picks the droppable whose box holds the pointer,
// not the neighbour the dragged handle's box overlaps most.
import { describe, expect, it } from "vitest";
import { pointerWithin, rectIntersection } from "@dnd-kit/core";
import { FOLDER_COLLISION } from "../FolderDnd";

const rect = (top, left = 0, width = 200, height = 32) => ({
  top,
  left,
  width,
  height,
  bottom: top + height,
  right: left + width,
});
const droppable = (id) => ({ id, data: { current: {} } });

describe("folder drop target", () => {
  it("is pointerWithin", () => {
    expect(FOLDER_COLLISION).toBe(pointerWithin);
  });

  it("picks the folder under the pointer where rect intersection picks its neighbour", () => {
    const containers = [
      droppable("folder:billing"),
      droppable("folder:support"),
    ];
    const rects = new Map([
      ["folder:billing", rect(100)],
      ["folder:support", rect(132)],
    ]);
    // The dragged handle's box sits mostly over "support" while the pointer
    // is inside "billing".
    const args = {
      active: { id: "dataset-drag:ds-1" },
      collisionRect: rect(120, 0, 200, 40),
      droppableRects: rects,
      droppableContainers: containers,
      pointerCoordinates: { x: 50, y: 118 },
    };
    expect(FOLDER_COLLISION(args)[0]?.id).toBe("folder:billing");
    expect(rectIntersection(args)[0]?.id).toBe("folder:support");
  });
});
