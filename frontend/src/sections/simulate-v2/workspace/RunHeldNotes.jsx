import { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { heldReasonOf, nameList } from "../_mock/heldReasons";
import { scenarioTitle } from "../_mock/scenarios";

/* Under the Run dialog's verdict: the held-back scenarios by name, each with
   a plain reason and the check's own words one click away; then the ones
   that changed since they were last proved (handover, Karthik 19 Nov). */
export default function RunHeldNotes({ report, scenarios, staleIds, envHeld }) {
  const [openId, setOpenId] = useState(null);
  /* Held for this agent (readiness) and held on this environment version. */
  const held = [
    ...(report?.scope?.blocked || []).map((b) => ({
      id: b.id,
      title: b.title,
      ...heldReasonOf(b, report.rows),
    })),
    ...(envHeld || []).map((b) => ({
      ...b,
      plain: b.plain
        ? `${b.plain.replace(/\.$/, "")}.`
        : "It can't run on this environment version.",
    })),
  ];
  const byId = new Map((scenarios || []).map((s) => [s.id, s]));
  /* The name the Scenarios table shows. */
  const nameOf = (id, fallback) => {
    const row = byId.get(id);
    return (
      row?.summary ||
      scenarioTitle(row?.title) ||
      row?.name ||
      fallback ||
      "A scenario"
    );
  };
  /* Same name under different rules: add the rule, as the table's group does. */
  const staleRaw = (staleIds || []).map((id) => nameOf(id));
  const staleNames = (staleIds || []).map((id, i) => {
    const dup = staleRaw.filter((n) => n === staleRaw[i]).length > 1;
    const rule = String(scenarioTitle(byId.get(id)?.title) || "").replace(
      /\.+$/,
      "",
    );
    return dup && rule && rule !== staleRaw[i]
      ? `${staleRaw[i]} (${rule})`
      : staleRaw[i];
  });
  if (!held.length && !staleNames.length) return null;

  return (
    <Stack spacing={1.25}>
      {held.length > 0 && (
        <Box>
          <Typography
            sx={{ typography: "s2", color: "text.secondary", mb: 0.5 }}
          >
            {`Held back · ${held.length} — never sent, recorded as not run`}
          </Typography>
          <Stack spacing={0.75}>
            {held.map((b) => {
              const r = b;
              const open = openId === b.id;
              return (
                <Box key={b.id}>
                  <Stack direction="row" spacing={1} alignItems="baseline">
                    <Typography
                      sx={{ typography: "s2", fontWeight: 500, minWidth: 0 }}
                    >
                      {nameOf(b.id, b.title)}
                    </Typography>
                    <Box sx={{ flex: 1 }} />
                    {r.detail && (
                      <ButtonBase
                        onClick={() => setOpenId(open ? null : b.id)}
                        aria-expanded={open}
                        sx={{
                          typography: "s3",
                          color: "text.secondary",
                          gap: 0.25,
                          flexShrink: 0,
                          "&:hover": { color: "text.primary" },
                        }}
                      >
                        {open ? "Hide check" : "Check details"}
                        <Iconify
                          icon={
                            open
                              ? "solar:alt-arrow-up-linear"
                              : "solar:alt-arrow-down-linear"
                          }
                          width={12}
                        />
                      </ButtonBase>
                    )}
                  </Stack>
                  <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                    {r.plain}
                  </Typography>
                  <Collapse in={open} unmountOnExit>
                    <Typography
                      sx={{
                        typography: "s3",
                        fontFamily: "monospace",
                        color: "text.secondary",
                        whiteSpace: "pre-line",
                        mt: 0.5,
                        px: 1,
                        py: 0.75,
                        borderRadius: 0.5,
                        bgcolor: "action.hover",
                      }}
                    >
                      {r.detail}
                    </Typography>
                  </Collapse>
                </Box>
              );
            })}
          </Stack>
        </Box>
      )}
      {staleNames.length > 0 && (
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`${staleNames.length} ${
            staleNames.length === 1 ? "scenario changed" : "scenarios changed"
          } since ${staleNames.length === 1 ? "it was" : "they were"} last proved: ${nameList(
            staleNames,
          )}. Validate ${staleNames.length === 1 ? "it" : "them"} before relying on the results.`}
        </Typography>
      )}
    </Stack>
  );
}

RunHeldNotes.propTypes = {
  report: PropTypes.object,
  scenarios: PropTypes.array,
  staleIds: PropTypes.array,
  envHeld: PropTypes.array,
};
