import { useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { heldReasonOf } from "../_mock/heldReasons";
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
  /* Changed since last proved: the name, with its rule in grey (same-named
     scenarios sit under different rules). */
  const stale = (staleIds || []).map((id) => ({
    id,
    name: nameOf(id),
    rule: String(scenarioTitle(byId.get(id)?.title) || "").replace(/\.+$/, ""),
  }));
  /* Same name under different rules reads as one line, its rules below. */
  const groups = Object.values(
    stale.reduce((m, x) => {
      (m[x.name] = m[x.name] || { name: x.name, rules: [] }).rules.push(
        x.rule && x.rule !== x.name ? x.rule : "",
      );
      return m;
    }, {}),
  );
  if (!held.length && !stale.length) return null;
  const SHOW = 3;

  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      {held.length > 0 && (
        <NoteRow
          label="Held back"
          count={held.length}
          tip="Never sent — recorded as not run"
        >
          <Stack spacing={0.75}>
            {held.map((b) => {
              const open = openId === b.id;
              return (
                <Box key={b.id} minWidth={0}>
                  <Stack direction="row" spacing={1} alignItems="baseline">
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        fontWeight: 500,
                        minWidth: 0,
                        flex: 1,
                      }}
                    >
                      {nameOf(b.id, b.title)}
                    </Typography>
                    {b.detail && (
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
                    {b.plain}
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
                      {b.detail}
                    </Typography>
                  </Collapse>
                </Box>
              );
            })}
          </Stack>
        </NoteRow>
      )}
      {stale.length > 0 && (
        <NoteRow
          label="Changed since proved"
          count={stale.length}
          tip="Validate them before relying on the results"
        >
          <Stack spacing={0.5}>
            {groups.slice(0, SHOW).map((g) => (
              <Box key={g.name} minWidth={0}>
                <Typography noWrap title={g.name} sx={{ typography: "s2" }}>
                  {g.name}
                  {g.rules.length > 1 && (
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      {` × ${g.rules.length}`}
                    </Box>
                  )}
                </Typography>
                {g.rules.some(Boolean) && (
                  <Typography
                    noWrap
                    title={g.rules.filter(Boolean).join("\n")}
                    sx={{ typography: "s3", color: "text.disabled" }}
                  >
                    {g.rules.filter(Boolean).join(" · ")}
                  </Typography>
                )}
              </Box>
            ))}
            {groups.length > SHOW && (
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {`+${groups.length - SHOW} more`}
              </Typography>
            )}
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              Validate them before relying on the results.
            </Typography>
          </Stack>
        </NoteRow>
      )}
    </Box>
  );
}

function NoteRow({ label, count, tip, children }) {
  return (
    <Box
      sx={{
        display: "grid",
        rowGap: 0.5,
        px: 1.5,
        py: 1,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography
        title={tip}
        sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
      >
        {label}
        <Box component="span" sx={{ color: "text.disabled" }}>
          {` · ${count}`}
        </Box>
      </Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}
NoteRow.propTypes = {
  label: PropTypes.string,
  count: PropTypes.number,
  tip: PropTypes.string,
  children: PropTypes.node,
};

RunHeldNotes.propTypes = {
  report: PropTypes.object,
  scenarios: PropTypes.array,
  staleIds: PropTypes.array,
  envHeld: PropTypes.array,
};
