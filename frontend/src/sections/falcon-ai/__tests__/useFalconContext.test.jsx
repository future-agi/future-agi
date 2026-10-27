import React from "react";
import PropTypes from "prop-types";
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { useFalconContext } from "../hooks/useFalconContext";

function wrapperFor(path) {
  function Wrapper({ children }) {
    return <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>;
  }

  Wrapper.propTypes = {
    children: PropTypes.node,
  };

  return Wrapper;
}

describe("useFalconContext", () => {
  it("classifies annotation queue detail pages as annotation queue context", () => {
    const { result } = renderHook(() => useFalconContext(), {
      wrapper: wrapperFor(
        "/dashboard/annotations/queues/queue-123/annotate?item=item-1",
      ),
    });

    expect(result.current).toEqual({
      page: "annotation_queues",
      path: "/dashboard/annotations/queues/queue-123/annotate",
      entity_type: "annotation_queue",
      entity_id: "queue-123",
    });
  });

  it("classifies annotation queue and label list pages separately", () => {
    const queueList = renderHook(() => useFalconContext(), {
      wrapper: wrapperFor("/dashboard/annotations/queues"),
    });
    const labelList = renderHook(() => useFalconContext(), {
      wrapper: wrapperFor("/dashboard/annotations/labels"),
    });

    expect(queueList.result.current).toMatchObject({
      page: "annotation_queues",
      path: "/dashboard/annotations/queues",
    });
    expect(labelList.result.current).toMatchObject({
      page: "annotation_labels",
      path: "/dashboard/annotations/labels",
    });
  });
});
