import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Checkbox,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { StatusDot } from "../components/primitives";
import { fieldFixOf, fixKindOf, fixLabelOf, shortFixLabel } from "./fixKinds";

/*
  Two or more blockers (13 Nov, manager's review): one row and one Fix
  instead of a Fix on every row. Fix opens a checklist — the fixes made here
  start ticked, a field fix takes its value in the row, a change on the agent
  stays a link — and Apply saves the ticked ones together, then runs the
  pair's check once (each check can take up to 45 minutes).
*/
const IN_PLACE = ["field", "tools", "release", "rebuild"];

const describe = (kind, row) =>
  kind === "tools"
    ? row.fixLabel || "Point its tools at the test world"
    : kind === "release"
      ? "Run the environment's release checks"
      : kind === "rebuild"
        ? "Rebuild the environment for this agent"
        : null;

export default function BlockerGroup({ areas, kit, onFix, skippedIds }) {
  const items = areas.map((a) => {
    const kind = a.fixRow ? fixKindOf(a.fixRow, kit) || "link" : null;
    const skips = a.affected.filter((id) => skippedIds.has(id)).length;
    const field = kind === "field" ? fieldFixOf(a.fixRow, kit) : null;
    return { a, kind, skips, field };
  });
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState(() =>
    Object.fromEntries(items.map((x) => [x.a.id, IN_PLACE.includes(x.kind)])),
  );
  const [values, setValues] = useState(() =>
    Object.fromEntries(
      items.filter((x) => x.field).map((x) => [x.a.id, x.field.value || ""]),
    ),
  );
  const skipped = new Set(
    areas.flatMap((a) => a.affected.filter((id) => skippedIds.has(id))),
  ).size;
  const chosen = items.filter(
    (x) =>
      IN_PLACE.includes(x.kind) &&
      picked[x.a.id] &&
      (x.kind !== "field" || (values[x.a.id] || "").trim()),
  );
  const batchable = items.some((x) => IN_PLACE.includes(x.kind));
  const apply = () => {
    const repairs = chosen
      .filter((x) => x.kind === "field" || x.kind === "tools")
      .map((x) => ({ fixId: x.a.fixRow.fixId, value: values[x.a.id] }));
    if (repairs.length) kit.applyRepairs(repairs);
    if (chosen.some((x) => x.kind === "release")) kit.runReleaseChecks?.();
    if (chosen.some((x) => x.kind === "rebuild")) kit.rebuild?.();
    setOpen(false);
  };

  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
      <Tooltip
        arrow
        placement="top-start"
        title={
          open ? (
            ""
          ) : (
            <Box sx={{ whiteSpace: "pre-line" }}>
              {areas.map((a) => `${a.label}: ${a.detail}`).join("\n")}
            </Box>
          )
        }
      >
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{ px: 1.5, py: 0.625 }}
        >
          <Box sx={{ pt: "7px", flexShrink: 0, display: "flex" }}>
            <StatusDot status="failed" size={6} />
          </Box>
          <Typography
            sx={{ typography: "s2", flex: 1, minWidth: 0, lineHeight: "22px" }}
          >
            {areas.map((a) => a.label).join(" · ")}
            {skipped > 0 && (
              <Box
                component="span"
                sx={{ typography: "s3", color: "text.tertiary", ml: 1 }}
              >
                {`${skipped} blocked`}
              </Box>
            )}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            endIcon={
              <Iconify
                icon={
                  open
                    ? "solar:alt-arrow-up-linear"
                    : "solar:alt-arrow-down-linear"
                }
                width={14}
              />
            }
            sx={{ py: 0, minWidth: 0, flexShrink: 0 }}
          >
            {open ? "Close" : `Fix ${areas.length}`}
          </Button>
        </Stack>
      </Tooltip>

      {open && (
        <Box sx={{ px: 1.5, pb: 1.25 }}>
          {items.map((x) => (
            <Stack
              key={x.a.id}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ minHeight: 36, pl: 1.25 }}
            >
              {IN_PLACE.includes(x.kind) ? (
                <Checkbox
                  size="small"
                  checked={!!picked[x.a.id]}
                  onChange={(e) =>
                    setPicked((p) => ({ ...p, [x.a.id]: e.target.checked }))
                  }
                  inputProps={{ "aria-label": x.a.label }}
                  sx={{ p: 0.5, ml: -0.5 }}
                />
              ) : (
                <Box sx={{ width: 24, flexShrink: 0 }} />
              )}
              <Box sx={{ flex: 1, minWidth: 0 }}>
                {x.kind === "field" ? (
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Typography
                      sx={{ typography: "s2", width: 120, flexShrink: 0 }}
                    >
                      {x.field.label}
                    </Typography>
                    <TextField
                      size="small"
                      value={values[x.a.id] || ""}
                      onChange={(e) =>
                        setValues((v) => ({ ...v, [x.a.id]: e.target.value }))
                      }
                      inputProps={{ "aria-label": x.field.label }}
                      sx={{ flex: 1, "& input": { typography: "s2", py: 0.5 } }}
                    />
                  </Stack>
                ) : x.kind === "link" ? (
                  <Button
                    size="small"
                    variant="text"
                    onClick={() => onFix?.(x.a.fixRow)}
                    endIcon={
                      <Iconify icon="solar:arrow-right-linear" width={12} />
                    }
                    sx={{ px: 0.5, ml: -0.5, textAlign: "left" }}
                  >
                    {shortFixLabel(fixLabelOf(x.a.fixRow)) ||
                      "Open the connection"}
                  </Button>
                ) : (
                  <Typography sx={{ typography: "s2" }}>
                    {describe(x.kind, x.a.fixRow) || x.a.label}
                  </Typography>
                )}
              </Box>
              <Typography
                sx={{
                  typography: "s3",
                  color: "text.tertiary",
                  flexShrink: 0,
                  textAlign: "right",
                }}
              >
                {[
                  x.kind === "link" ? "on the agent" : null,
                  x.skips ? `${x.skips} blocked` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Stack>
          ))}
          {batchable && (
            <Stack
              direction="row"
              spacing={1}
              justifyContent="flex-end"
              sx={{ mt: 0.75 }}
            >
              {/* Close on the row dismisses; this only applies. */}
              <Button
                size="small"
                variant="outlined"
                disabled={!chosen.length}
                onClick={apply}
              >
                {`Apply ${chosen.length} and re-check`}
              </Button>
            </Stack>
          )}
        </Box>
      )}
    </Box>
  );
}
BlockerGroup.propTypes = {
  areas: PropTypes.array.isRequired,
  /* { spec, agent, applyRepairs([{ fixId, value }]), runReleaseChecks, rebuild } */
  kit: PropTypes.object,
  onFix: PropTypes.func,
  skippedIds: PropTypes.instanceOf(Set).isRequired,
};
