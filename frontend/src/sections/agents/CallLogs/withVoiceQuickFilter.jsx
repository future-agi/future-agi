import React from "react";
import QuickFilter from "src/components/ComplexFilter/QuickFilterComponents/QuickFilter";

// Adds the hover filter affordance to a voice cell renderer. Columns opt in by
// carrying `context.sourceColumn` on their colDef.
const withVoiceQuickFilter = (Renderer, getFilterValue) => {
  const Wrapped = (params) => {
    const content = <Renderer {...params} />;
    const col = params?.colDef?.context?.sourceColumn;
    const applyQuickFilters = params?.applyQuickFilters;
    if (!col || !applyQuickFilters) return content;

    const value = getFilterValue ? getFilterValue(params) : params?.value;
    if (value === null || value === undefined || value === "") return content;

    return (
      <QuickFilter
        onClick={(e) => {
          // Rows open the call drawer on click; keep the filter button local.
          e.stopPropagation();
          applyQuickFilters({
            col,
            value,
            filterAnchor: { top: e.clientY, left: e.clientX },
          });
        }}
      >
        {/* Must be a DOM element: Tooltip clones the child to attach handlers. */}
        <div style={{ width: "100%", height: "100%" }}>{content}</div>
      </QuickFilter>
    );
  };

  Wrapped.displayName = `withVoiceQuickFilter(${
    Renderer.displayName || Renderer.name || "Cell"
  })`;
  return Wrapped;
};

export default withVoiceQuickFilter;
