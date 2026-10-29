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
  unmeasured: "Not measured",
};

/* One list: search, select all/none, back to the suggestion, then rows. */
function CaseList({ title, note, items, chosen, onChange, emptyText }) {
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
        {!isSuggested && (
          <TextLink onClick={() => onChange(suggested)}>
            Back to suggested
          </TextLink>
        )}
        <TextLink onClick={() => onChange(items.map((x) => x.id))}>
          All
        </TextLink>
        <TextLink onClick={() => onChange([])}>None</TextLink>
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
                {...clickable(() => toggle(x.id), {
                  role: "checkbox",
                  checked: on,
                })}
                sx={{
                  ...focusRing,
                  px: 1.25,
                  height: 32,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                  ...(i > 0 && {
                    borderTop: "1px solid",
                    borderColor: "divider",
                  }),
                }}
              >
                <Checkbox
                  size="small"
                  checked={on}
                  tabIndex={-1}
                  disableRipple
                  inputProps={{ "aria-hidden": true }}
                  sx={{ p: 0 }}
                />
                <Typography
                  noWrap
                  sx={{ typography: "s2", flex: 1, minWidth: 0 }}
                >
                  {x.title}
                </Typography>
                {x.held && (
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      flexShrink: 0,
                    }}
                  >
                    Held back
                  </Typography>
                )}
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
};

/**
 * Edit plan → Cases to fix and Cases to protect (Fix my agent v0): start on
 * the suggestion — the failing cases as targets (some held back to prove the
 * fix), and every case passing each repeat today as protected — and let the
 * person change either list. Held-back cases stay held back; unticking a
 * case leaves it out of this improvement, it isn't deleted.
 */
export default function CasePicker({ pickable, pick, onChange }) {
  const fix =
    pick?.fix || pickable.fix.filter((x) => x.suggested).map((x) => x.id);
  const protect =
    pick?.protect ||
    pickable.protect.filter((x) => x.suggested).map((x) => x.id);
  return (
    <Stack spacing={2}>
      <CaseList
        title="Cases to fix"
        note="The failing cases this improvement works on. Held-back ones prove the fix — it never sees them."
        items={pickable.fix}
        chosen={fix}
        onChange={(ids) => onChange({ fix: ids, protect })}
        emptyText="Nothing is failing — there's nothing to fix."
      />
      <CaseList
        title="Cases to protect"
        note="Cases that must keep passing. Suggested: every case that passes all its repeats today."
        items={pickable.protect}
        chosen={protect}
        onChange={(ids) => onChange({ fix, protect: ids })}
        emptyText="No other cases to protect."
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
