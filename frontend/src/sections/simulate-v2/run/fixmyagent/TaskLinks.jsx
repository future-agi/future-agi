import PropTypes from "prop-types";
import { Stack, Chip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * The scenarios behind a number, so the number can be opened.
 *
 * A finding that says "3 tasks" makes the reader go and find which three. Every
 * count in the diagnosis names them instead, and clicking one opens its trace
 * at the step the finding is about.
 */
export default function TaskLinks({ ids, tasks, step, onOpen }) {
  const named = (ids || []).map((id) => tasks.find((t) => t.id === id)).filter(Boolean);
  if (!named.length) return null;

  return (
    <Stack direction="row" flexWrap="wrap" gap={0.625} sx={{ mt: 0.625 }}>
      {named.map((t) => (
        <Chip
          key={t.id}
          size="small"
          label={t.title}
          /* Inside an analyzer row, which is itself a toggle — without this,
             opening the evidence also collapses the finding you opened it from. */
          onClick={onOpen ? (e) => { e.stopPropagation(); onOpen(t, step); } : undefined}
          icon={<Iconify icon="solar:arrow-right-up-linear" width={12} />}
          sx={{
            height: 20, borderRadius: 0.5, maxWidth: 260,
            border: "1px solid", borderColor: "divider", bgcolor: "transparent",
            color: "text.secondary", cursor: onOpen ? "pointer" : "default",
            "& .MuiChip-icon": { ml: 0.625, mr: -0.25, color: "text.disabled" },
            "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
            "&:hover": { borderColor: "text.disabled", color: "text.primary" },
          }}
        />
      ))}
    </Stack>
  );
}

TaskLinks.propTypes = {
  ids: PropTypes.array,
  tasks: PropTypes.array,
  step: PropTypes.string,
  onOpen: PropTypes.func,
};
