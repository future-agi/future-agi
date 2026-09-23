import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import Iconify from "src/components/iconify";

/**
 * Wraps a whole section (header + its widget grid) so the user can
 * drag the section by its header and reorder entire sections
 * top-to-bottom. Only the section header (title + drag handle) is
 * the drag surface; the widgets inside stay their own drag sources
 * for within/across-section widget moves.
 */
export default function SortableSection({ sectionId, label, hint, children }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `section:${sectionId}`,
    data: { kind: "section", sectionId },
  });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 3 : "auto",
    opacity: isDragging ? 0.55 : 1,
  };
  return (
    <Box ref={setNodeRef} style={style} className="analytics-section" data-section-id={sectionId}>
      {/* Section header — this row is the drag handle. The rest of
          the section (widget grid below) doesn't listen for section
          drag, so widget-level drag inside still works normally. */}
      <Box
        {...attributes} {...listeners}
        className="section-drag-surface analytics-no-print"
        sx={{
          pt: 2, mt: 0.5, mb: 0.5, px: 0.25,
          borderTop: "1px solid",
          borderColor: (t) => alpha(t.palette.text.primary, t.palette.mode === "dark" ? 0.08 : 0.06),
          cursor: isDragging ? "grabbing" : "grab",
          touchAction: "none",
          userSelect: "none",
          "&:hover .section-drag-icon": { opacity: 1 },
          borderRadius: 1,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={1}>
          <Box
            className="section-drag-icon"
            sx={{
              width: 18, height: 18, display: "grid", placeItems: "center",
              color: "text.subtitle",
              opacity: 0.35,
              transition: "opacity 120ms",
            }}
            aria-hidden
          >
            <Iconify icon="solar:hamburger-menu-linear" width={14} />
          </Box>
          <Typography sx={{
            fontSize: 18, fontWeight: 700, letterSpacing: -0.2, lineHeight: 1.2,
            color: "text.primary",
          }}>
            {label}
          </Typography>
        </Stack>
        {hint && (
          <Typography sx={{ typography: "s2", color: "text.subtitle", mt: 0.25, pl: "26px" }}>
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
