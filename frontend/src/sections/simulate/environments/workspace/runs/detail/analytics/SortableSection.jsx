import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";

// One analytics section. Its header is the handle that drags the whole
// section; the widgets below stay their own drag sources.
export default function SortableSection({ sectionId, label, hint, children }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: `section:${sectionId}`,
    data: { kind: "section", sectionId },
  });
  return (
    <Box
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        zIndex: isDragging ? 3 : "auto",
        opacity: isDragging ? 0.55 : 1,
      }}
      className="analytics-section"
      data-section-id={sectionId}
    >
      <Box
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-roledescription="Sortable section"
        aria-label={`${label} section`}
        sx={{
          pt: 2,
          mt: 0.5,
          mb: 1.5,
          px: 0.25,
          borderTop: "1px solid",
          borderColor: "divider",
          cursor: isDragging ? "grabbing" : "grab",
          touchAction: "none",
          userSelect: "none",
          "&:hover .section-drag-icon": { opacity: 1 },
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box
            className="section-drag-icon analytics-no-print"
            aria-hidden
            sx={{
              width: 18,
              height: 18,
              display: "grid",
              placeItems: "center",
              color: "text.subtitle",
              opacity: 0.35,
              transition: "opacity 120ms",
            }}
          >
            <Iconify icon="solar:hamburger-menu-linear" width={14} />
          </Box>
          <Typography
            component="h2"
            sx={{
              fontSize: 16,
              fontWeight: 600,
              letterSpacing: -0.2,
              lineHeight: 1.2,
              color: "text.primary",
            }}
          >
            {label}
          </Typography>
        </Stack>
        {hint && (
          <Typography
            sx={{
              typography: "s2",
              color: "text.subtitle",
              mt: 0.25,
              pl: 3.25,
            }}
          >
            {hint}
          </Typography>
        )}
      </Box>
      {children}
    </Box>
  );
}
SortableSection.propTypes = {
  sectionId: PropTypes.string.isRequired,
  label: PropTypes.node,
  hint: PropTypes.node,
  children: PropTypes.node,
};
