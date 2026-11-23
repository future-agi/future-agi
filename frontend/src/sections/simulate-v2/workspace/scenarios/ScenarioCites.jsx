import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Button, Stack, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useEnvState } from "../../store";
import { refreshPlanOf, regroundPatch } from "../../_mock/orgData";
import {
  keepPinnedGroundPatch,
  regroundPatch as regroundEvidencePatch,
} from "../../_mock/scenarioOrg";
import { keepCaseOnSourcePatch } from "../../_mock/sourceChange";
import { nextEnvLabel } from "../../_mock/envSnapshot";
import { CITE_STATUS_LABEL, citationsOf } from "../../_mock/scenarioCitations";
import ReviewRefreshDialog from "../data/ReviewRefreshDialog";

/**
 * The drawer's "Cites" rows: each rule and file the scenario rests on, and
 * whether it still holds — Confirmed, Changed since, or Couldn't be
 * confirmed. A Changed since row opens Compare: the old text next to the new,
 * with what to do about it (move to the new version, keep this case on the
 * old one, review a Knowledge base refresh).
 *
 * Rendered inside the drawer's ruled sheet: every row is a `.sr`, the same
 * label gutter as the rest of it. `openKey`/`onOpen` let the drawer's
 * "Source changed" line open a row's Compare; without them it keeps its own.
 */
export default function ScenarioCites({
  row,
  env,
  envState,
  goals,
  locked = false,
  openKey,
  onOpen,
}) {
  const store = useEnvState(env.id);
  const [ownKey, setOwnKey] = useState(null);
  const open = onOpen ? openKey : ownKey;
  const setOpen = onOpen || setOwnKey;
  const cites = citationsOf(row, {
    env,
    envState: envState || store.envState,
    goals,
  });
  if (!cites.length)
    return (
      <CiteRow label="None">
        <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
          No citations — it doesn&apos;t rest on a rule or a file.
        </Typography>
      </CiteRow>
    );
  return cites.map((c) => (
    <CiteRow key={c.key} label={c.kind}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: "minmax(0, 1fr) auto",
          columnGap: 1.5,
          alignItems: "baseline",
        }}
      >
        <Box minWidth={0}>
          <Typography sx={{ typography: "s2", fontWeight: 500 }}>
            {c.name}
          </Typography>
          {c.place && (
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {c.place}
            </Typography>
          )}
        </Box>
        <Stack direction="row" alignItems="baseline" spacing={1.25}>
          <Typography
            sx={{
              typography: "s3",
              fontWeight: 500,
              whiteSpace: "nowrap",
              color:
                c.status === "changed"
                  ? "status.warning.fg"
                  : c.status === "unconfirmed"
                    ? "text.secondary"
                    : "text.subtitle",
            }}
          >
            {CITE_STATUS_LABEL[c.status]}
          </Typography>
          {c.status === "changed" && (c.compare || c.fix) && (
            <QuietLink onClick={() => setOpen(open === c.key ? null : c.key)}>
              {open === c.key ? "Hide" : "Compare"}
            </QuietLink>
          )}
        </Stack>
      </Box>
      {c.note && (
        <Typography
          sx={{ typography: "s3", color: "text.secondary", mt: 0.25 }}
        >
          {c.note}
        </Typography>
      )}
      {open === c.key && (
        <Box sx={{ mt: 1 }}>
          {c.compare?.pairs?.length > 0 && <Compare compare={c.compare} />}
          {!locked && c.fix && (
            <CiteFix
              fix={c.fix}
              row={row}
              env={env}
              envState={store.envState}
              patch={store.patch}
            />
          )}
        </Box>
      )}
    </CiteRow>
  ));
}
ScenarioCites.propTypes = {
  row: PropTypes.object.isRequired,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  goals: PropTypes.array,
  locked: PropTypes.bool,
  openKey: PropTypes.string,
  onOpen: PropTypes.func,
};

/* The drawer sheet's ruled row: the label in a fixed gutter, the value beside it. */
function CiteRow({ label, children }) {
  return (
    <Box
      className="sr"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "124px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.25,
        px: 1.75,
        py: 1.125,
      }}
    >
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        {label}
      </Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}
CiteRow.propTypes = { label: PropTypes.string, children: PropTypes.node };

/* Old text beside new: the old struck and muted, the new as written. */
function Compare({ compare }) {
  const head = {
    typography: "s3",
    color: "text.subtitle",
    fontWeight: 500,
  };
  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
        columnGap: 2,
        rowGap: 0.75,
        py: 1,
        borderTop: "1px solid",
        borderBottom: "1px solid",
        borderColor: "divider",
      }}
    >
      <Typography sx={head}>{compare.was}</Typography>
      <Typography sx={head}>{compare.now}</Typography>
      {compare.pairs.map((p) => [
        <Typography
          key={`${p.key}-from`}
          sx={{
            typography: "s2",
            color: "text.disabled",
            textDecoration: p.from ? "line-through" : "none",
          }}
        >
          {p.from
            ? `${p.place ? `${p.place} ` : ""}“${p.from}”`
            : "Not in this version"}
        </Typography>,
        <Typography key={`${p.key}-to`} sx={{ typography: "s2" }}>
          {p.to ? `${p.place ? `${p.place} ` : ""}“${p.to}”` : "Removed"}
        </Typography>,
      ])}
    </Box>
  );
}
Compare.propTypes = { compare: PropTypes.object };

/* What a changed citation offers — the actions the Source changed notice had. */
function CiteFix({ fix, row, env, envState, patch }) {
  const [reviewing, setReviewing] = useState(false);
  if (fix.type === "kb")
    return (
      <>
        <Button
          size="small"
          variant="outlined"
          onClick={() => setReviewing(true)}
          sx={{ mt: 1 }}
        >
          Review refresh
        </Button>
        {reviewing && (
          <ReviewRefreshDialog
            open
            onClose={() => setReviewing(false)}
            env={env}
            envState={envState}
            patch={patch}
            sourceId={fix.source.id}
          />
        )}
      </>
    );
  if (fix.type === "ground")
    return (
      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
        <Button
          size="small"
          variant="outlined"
          onClick={() => patch(regroundEvidencePatch(envState, row.id, fix.id))}
        >
          Re-check evidence
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          onClick={() => patch(keepPinnedGroundPatch(envState, row.id, fix.id))}
        >
          Keep pinned
        </Button>
      </Stack>
    );
  const { source, pending } = fix;
  const plan = refreshPlanOf(env, envState, source, { name: pending.name });
  const next = nextEnvLabel(env, envState);
  return (
    <>
      <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => {
            patch(regroundPatch(env, envState, source, plan));
            enqueueSnackbar(
              `Moved to ${source.name} v${pending.v} as environment ${next}. ${plan.affected.length} case${plan.affected.length === 1 ? "" : "s"} to re-check.`,
              { variant: "success" },
            );
          }}
        >
          {`Move to v${pending.v} · saves environment ${next}`}
        </Button>
        <Button
          variant="outlined"
          size="small"
          color="inherit"
          onClick={() => patch(keepCaseOnSourcePatch(envState, row.id, source))}
        >
          {`Keep this case on v${source.version}`}
        </Button>
      </Stack>
      {plan.affected.length > 1 && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", mt: 0.75 }}>
          {`Moving re-checks ${plan.affected.length} cases that touch this file.`}
        </Typography>
      )}
    </>
  );
}
CiteFix.propTypes = {
  fix: PropTypes.object.isRequired,
  row: PropTypes.object.isRequired,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
};

/* A quiet inline link, as the drawer's "Details". */
function QuietLink({ onClick, children }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{
        p: 0,
        border: 0,
        bgcolor: "transparent",
        cursor: "pointer",
        font: "inherit",
        typography: "s3",
        fontWeight: 600,
        color: "text.primary",
        whiteSpace: "nowrap",
        "&:hover": { textDecoration: "underline" },
      }}
    >
      {children}
    </Box>
  );
}
QuietLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node };
