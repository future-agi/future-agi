import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Checkbox, InputBase, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { clickable, focusRing } from "../components/clickable";
import { TextLink } from "../agents/connect/connectBits";

const STATUS = {
  failing: "Failing",
  failed: "Failing",
  mixed: "Flaky",
  flaky: "Flaky",
  passing: "Passing",
  passed: "Passing",
  unmeasured: "Couldn't measure",
};

/* One list: search, select all/none, back to the suggestion, then rows. */
function CaseList({
  title,
  note,
  items,
  chosen,
  onChange,
  emptyText,
  readOnly,
}) {
  const [q, setQ] = useState("");
  const set = new Set(chosen);
  const shown = items.filter(
    (x) => !q.trim() || x.title.toLowerCase().includes(q.trim().toLowerCase()),
  );
  const suggested = items.filter((x) => x.suggested).map((x) => x.id);
  const isSuggested =
    suggested.length === chosen.length && suggested.every((id) => set.has(id));
  const toggle = (id) =>
    onChange(set.has(id) ? chosen.filter((x) => x !== id) : [...chosen, id]);
  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 0.5 }}>
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {`${chosen.length} of ${items.length}`}
        </Typography>
        {!readOnly && !isSuggested && (
          <TextLink onClick={() => onChange(suggested)}>
            Back to suggested
          </TextLink>
        )}
        {!readOnly && (
          <TextLink onClick={() => onChange(items.map((x) => x.id))}>
            All
          </TextLink>
        )}
        {!readOnly && <TextLink onClick={() => onChange([])}>None</TextLink>}
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.tertiary", mb: 0.75 }}>
        {note}
      </Typography>
      <Box
        sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}
      >
        {items.length > 8 && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.75}
            sx={{
              px: 1.25,
              height: 32,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:magnifer-linear"
              width={14}
              sx={{ color: "text.disabled" }}
            />
            <InputBase
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search cases"
              inputProps={{ "aria-label": `Search ${title}` }}
              sx={{ typography: "s2", flex: 1 }}
            />
          </Stack>
        )}
        <Box sx={{ maxHeight: 248, overflowY: "auto" }}>
          {shown.map((x, i) => {
            const on = set.has(x.id);
            return (
              <Stack
                key={x.id}
                direction="row"
                alignItems="center"
                spacing={1}
                {...(readOnly
                  ? {}
                  : clickable(() => toggle(x.id), {
                      role: "checkbox",
                      checked: on,
                    }))}
                sx={{
                  ...focusRing,
                  px: 1.25,
                  height: 32,
                  ...(!readOnly && {
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.hover" },
                  }),
                  ...(i > 0 && {
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }),
                }}
              >
                {!readOnly && (
                  <Checkbox
                    size="small"
                    checked={on}
                    tabIndex={-1}
                    disableRipple
                    inputProps={{ "aria-hidden": true }}
                    sx={{ p: 0 }}
                  />
                )}
                <Typography
                  noWrap
                  sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                >
                  {x.title}
                </Typography>
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    width: 84,
                    textAlign: "right",
                    flexShrink: 0,
                  }}
                >
                  {x.flakyToday ? "Flaky today" : STATUS[x.status] || ""}
                </Typography>
              </Stack>
            );
          })}
          {!shown.length && (
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", px: 1.25, py: 1 }}
            >
              {items.length ? "No case matches." : emptyText}
            </Typography>
          )}
        </Box>
      </Box>
    </Box>
  );
}
CaseList.propTypes = {
  title: PropTypes.string.isRequired,
  note: PropTypes.string,
  items: PropTypes.array.isRequired,
  chosen: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
  emptyText: PropTypes.string,
  readOnly: PropTypes.bool,
};

/**
 * Edit plan → three separate lists (Fix my agent v0): Targets (to fix),
 * Protected (must keep passing) and Held back (untouched, used to verify).
 * A case is in only one. Start on the suggestion; unticking a case leaves it
 * out of this improvement, it isn't deleted. Held-back cases aren't editable.
 */
export default function CasePicker({ pickable, pick, onChange }) {
  const heldItems = pickable.fix.filter((x) => x.held);
  const heldIds = heldItems.map((x) => x.id);
  const targets = pickable.fix.filter((x) => !x.held);
  const taken = new Set(pickable.fix.map((x) => x.id));
  const protectItems = pickable.protect.filter((x) => !taken.has(x.id));
  const fix = (
    pick?.fix || targets.filter((x) => x.suggested).map((x) => x.id)
  ).filter((id) => !heldIds.includes(id));
  const protect = (
    pick?.protect || protectItems.filter((x) => x.suggested).map((x) => x.id)
  ).filter((id) => !taken.has(id));
  return (
    <Stack spacing={2}>
      <CaseList
        title="Targets"
        note="Failing cases this improvement works on."
        items={targets}
        chosen={fix}
        onChange={(ids) => onChange({ fix: [...ids, ...heldIds], protect })}
        emptyText="Nothing is failing — there's nothing to fix."
      />
      <CaseList
        title="Protected"
        note="Cases that must keep passing."
        items={protectItems}
        chosen={protect}
        onChange={(ids) =>
          onChange({ fix: [...fix, ...heldIds], protect: ids })
        }
        emptyText="No other cases to protect."
      />
      <CaseList
        readOnly
        title="Held back"
        note="Untouched — used to verify the fix."
        items={heldItems}
        chosen={heldIds}
        onChange={() => {}}
        emptyText="Nothing is held back."
      />
    </Stack>
  );
}
CasePicker.propTypes = {
  pickable: PropTypes.shape({ fix: PropTypes.array, protect: PropTypes.array })
    .isRequired,
  pick: PropTypes.object,
  onChange: PropTypes.func.isRequired,
};
