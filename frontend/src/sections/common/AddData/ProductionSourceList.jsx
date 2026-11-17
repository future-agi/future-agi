import PropTypes from "prop-types";
import { Checkbox, FormControlLabel, Stack, Typography } from "@mui/material";

function sourceGroups(items, kind) {
  if (kind !== "sessions")
    return items.map((item) => ({
      id: item.span_id,
      label: `${item.name} · ${item.span_id}`,
      items: [item],
    }));
  const groups = new Map();
  for (const item of items) {
    const id = item.session_id;
    if (!groups.has(id))
      groups.set(id, { id, label: `Session ${id}`, items: [] });
    groups.get(id).items.push(item);
  }
  return [...groups.values()];
}

export default function ProductionSourceList({
  items,
  kind,
  picked,
  onChange,
  disabled = false,
}) {
  return sourceGroups(items, kind).map((group) => {
    const ids = group.items.map((item) => item.span_id);
    const count = ids.filter((id) => picked.includes(id)).length;
    return (
      <Stack key={group.id}>
        <FormControlLabel
          label={
            kind === "sessions"
              ? `${group.label} (${ids.length} ${ids.length === 1 ? "trace" : "traces"})`
              : group.label
          }
          control={
            <Checkbox
              disabled={disabled}
              checked={count === ids.length}
              indeterminate={count > 0 && count < ids.length}
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...new Set([...picked, ...ids])]
                    : picked.filter((id) => !ids.includes(id)),
                )
              }
            />
          }
        />
        {kind === "sessions" && (
          <Typography variant="body2" color="text.secondary" sx={{ pl: 4 }}>
            {group.items.map((item) => item.name).join(", ")}
          </Typography>
        )}
      </Stack>
    );
  });
}
ProductionSourceList.propTypes = {
  items: PropTypes.array.isRequired,
  kind: PropTypes.string.isRequired,
  picked: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  disabled: PropTypes.bool,
};
