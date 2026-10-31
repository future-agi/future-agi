import { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box } from "@mui/material";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import Iconify from "src/components/iconify";
import SortablePanel from "./SortablePanel";
import SortableSection from "./SortableSection";
import { SECTIONS, defaultSpanOf } from "./panelRegistry";

function DragGhost({ label }) {
  return (
    <Box
      sx={{
        pointerEvents: "none",
        px: 1.5,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: (theme) => alpha(theme.palette.primary.main, 0.8),
        bgcolor: (theme) => alpha(theme.palette.background.paper, 0.98),
        color: "text.primary",
        typography: "s1",
        fontWeight: 600,
        boxShadow: (theme) => theme.customShadows?.z16 ?? theme.shadows[16],
        display: "inline-flex",
        alignItems: "center",
        gap: 1,
      }}
    >
      <Iconify icon="solar:hamburger-menu-linear" width={14} />
      {label}
    </Box>
  );
}
DragGhost.propTypes = { label: PropTypes.node };

const SECTION_PREFIX = "section:";
const isSection = (id) => String(id).startsWith(SECTION_PREFIX);
const sectionKey = (id) => String(id).slice(SECTION_PREFIX.length);

// Every visible widget, grouped by section in the view's section order. One
// drag context holds both: a section header drags its whole section, and a
// widget dropped on another section's widget (or header) moves there.
export default function LayoutBody({
  layout,
  ids,
  sectionOf,
  titleOf,
  renderWidget,
  actionsFor,
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );
  const [activeId, setActiveId] = useState(null);

  const bySection = useMemo(() => {
    const map = new Map();
    ids.forEach((id) => {
      const section = sectionOf(id);
      if (!map.has(section)) map.set(section, []);
      map.get(section).push(id);
    });
    return map;
  }, [ids, sectionOf]);

  const moveSection = (active, over) => {
    const target = isSection(over) ? sectionKey(over) : sectionOf(over);
    const order = layout.sectionOrder;
    const from = order.indexOf(sectionKey(active));
    const to = order.indexOf(target);
    if (from < 0 || to < 0 || from === to) return;
    layout.reorderSections(arrayMove(order, from, to));
  };

  const moveWidget = (active, over) => {
    const order = layout.visibleIds;
    const from = order.indexOf(active);
    if (from < 0) return;
    const target = isSection(over) ? sectionKey(over) : sectionOf(over);
    // Dropped on a header: the widget goes to the end of that section.
    const anchor = isSection(over)
      ? (bySection.get(target) || []).filter((id) => id !== active).at(-1)
      : over;
    const next = order.filter((id) => id !== active);
    const at = anchor == null ? next.length : next.indexOf(anchor);
    if (at < 0) return;
    next.splice(isSection(over) || at >= from ? at + 1 : at, 0, active);
    layout.reorderAndSection(
      next,
      active,
      target === sectionOf(active) ? null : target,
    );
  };

  const onDragEnd = ({ active, over }) => {
    setActiveId(null);
    if (!over || active.id === over.id) return;
    if (isSection(active.id)) moveSection(active.id, over.id);
    else moveWidget(active.id, over.id);
  };

  const sections = layout.sectionOrder
    .map((id) => SECTIONS.find((section) => section.id === id))
    .filter((section) => section && bySection.get(section.id)?.length);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={({ active }) => setActiveId(active?.id ?? null)}
      onDragCancel={() => setActiveId(null)}
      onDragEnd={onDragEnd}
    >
      <SortableContext
        items={sections.map((section) => `${SECTION_PREFIX}${section.id}`)}
        strategy={rectSortingStrategy}
      >
        {sections.map((section) => {
          const sectionIds = bySection.get(section.id);
          const columns = section.columns || 2;
          return (
            <SortableSection
              key={section.id}
              sectionId={section.id}
              label={section.label}
              hint={section.hint}
            >
              <SortableContext
                items={sectionIds}
                strategy={rectSortingStrategy}
              >
                <Box
                  sx={{
                    display: "grid",
                    gap: 1.5,
                    alignItems: "stretch",
                    gridTemplateColumns: {
                      xs: "1fr",
                      md: `repeat(${Math.min(columns, 2)}, minmax(0,1fr))`,
                      lg: `repeat(${columns}, minmax(0,1fr))`,
                    },
                  }}
                >
                  {sectionIds.map((id) => {
                    const widget = layout.customWidgets.find(
                      (item) => item.id === id,
                    );
                    // Measured in the section's slots: in a four-slot
                    // section 1 is a quarter, in a two-slot one a half.
                    const span = Math.min(
                      layout.spans[id] || defaultSpanOf(id, widget, columns),
                      columns,
                    );
                    return (
                      <Box
                        key={id}
                        sx={{
                          minWidth: 0,
                          gridColumn: {
                            md: span > 1 ? "1 / -1" : "auto",
                            lg: span > 1 ? `span ${span}` : "auto",
                          },
                        }}
                      >
                        <SortablePanel
                          id={id}
                          title={titleOf(id)}
                          sectionColumns={columns}
                          currentSpan={span}
                          hasOverrides={layout.hasOverrides(id)}
                          {...actionsFor(id, columns)}
                        >
                          {renderWidget(id)}
                        </SortablePanel>
                      </Box>
                    );
                  })}
                </Box>
              </SortableContext>
            </SortableSection>
          );
        })}
      </SortableContext>
      <DragOverlay dropAnimation={null}>
        {activeId ? (
          <DragGhost
            label={
              isSection(activeId)
                ? `Section: ${
                    SECTIONS.find(
                      (section) => section.id === sectionKey(activeId),
                    )?.label || sectionKey(activeId)
                  }`
                : titleOf(activeId)
            }
          />
        ) : null}
      </DragOverlay>
    </DndContext>
  );
}
LayoutBody.propTypes = {
  layout: PropTypes.object.isRequired,
  ids: PropTypes.arrayOf(PropTypes.string).isRequired,
  sectionOf: PropTypes.func.isRequired,
  titleOf: PropTypes.func.isRequired,
  renderWidget: PropTypes.func.isRequired,
  actionsFor: PropTypes.func.isRequired,
};
