import PropTypes from "prop-types";
import { Box, Chip, ListSubheader, MenuItem, Select } from "@mui/material";
import Iconify from "src/components/iconify";
import { PART_ICON, protectedPartOf } from "../_mock/agentConstraints";
import { MONO } from "./agentViewUtils";

/**
 * The parts no change may edit — files, tools and prompt sections — as
 * chips, with one picker grouped the same three ways for adding another.
 */
export default function ProtectedPartsPicker({ value, parts, onChange }) {
  const taken = new Set(value);
  const groups = [
    { id: "files", title: "Files", items: parts.files },
    { id: "tools", title: "Tools", items: parts.tools.map((t) => `tool:${t}`) },
    { id: "sections", title: "Prompt sections", items: parts.sections },
  ]
    .map((g) => ({ ...g, items: g.items.filter((x) => !taken.has(x)) }))
    .filter((g) => g.items.length);

  return (
    <Box
      sx={{
        display: "flex",
        flexWrap: "wrap",
        gap: 0.75,
        alignItems: "center",
      }}
    >
      {value.map((entry) => {
        const part = protectedPartOf(entry);
        return (
          <Chip
            key={entry}
            size="small"
            icon={<Iconify icon={PART_ICON[part.kind]} width={14} />}
            label={<ProtectedPartLabel entry={entry} />}
            onDelete={() => onChange(value.filter((x) => x !== entry))}
            sx={{
              color: "text.primary",
              bgcolor: "transparent",
              border: "1px solid",
              borderColor: "divider",
              "& .MuiChip-icon": { color: "text.secondary", ml: 0.75 },
            }}
          />
        );
      })}
      {groups.length > 0 && (
        <Select
          size="small"
          displayEmpty
          value=""
          onChange={(e) =>
            e.target.value && onChange([...value, e.target.value])
          }
          renderValue={() => "Protect a part…"}
          MenuProps={{ PaperProps: { sx: { maxHeight: 360 } } }}
          sx={{
            height: 30,
            minWidth: 140,
            typography: "s2",
            color: "text.subtitle",
            "& .MuiSelect-select": { typography: "s2", py: 0.5 },
          }}
        >
          {groups.flatMap((g) => [
            <ListSubheader
              key={g.id}
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.subtitle",
                lineHeight: "28px",
              }}
            >
              {g.title}
            </ListSubheader>,
            ...g.items.map((x) => (
              <MenuItem key={x} value={x} sx={{ typography: "s2" }}>
                <ProtectedPartLabel entry={x} />
              </MenuItem>
            )),
          ])}
        </Select>
      )}
    </Box>
  );
}

ProtectedPartsPicker.propTypes = {
  value: PropTypes.arrayOf(PropTypes.string).isRequired,
  /* From protectablePartsOf — { files, tools, sections }. */
  parts: PropTypes.shape({
    files: PropTypes.array,
    tools: PropTypes.array,
    sections: PropTypes.array,
  }).isRequired,
  onChange: PropTypes.func.isRequired,
};

/** A file's path, a tool's name, or "system.md › Closing" — names in mono, headings as words. */
export function ProtectedPartLabel({ entry }) {
  const part = protectedPartOf(entry);
  if (part.kind !== "section")
    return (
      <Box component="span" sx={{ fontFamily: MONO }}>
        {part.label}
      </Box>
    );
  return (
    <Box component="span">
      <Box component="span" sx={{ fontFamily: MONO }}>
        {part.file.replace(/^.*\//, "")}
      </Box>
      <Box component="span" sx={{ color: "text.subtitle", mx: 0.5 }}>
        ›
      </Box>
      {part.heading}
    </Box>
  );
}
ProtectedPartLabel.propTypes = { entry: PropTypes.string.isRequired };
