import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { segmentedToggleSx } from "../../agents/segmentedToggleSx";
import { readFile, sourceFromOrg } from "../../_mock/orgData";
import {
  CHANGE_KIND,
  kbRefreshPatch,
  kbRefreshPlanOf,
  validateKbRefresh,
} from "../../_mock/kbRefresh";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";
import AddSourceMenu from "./AddSourceMenu";

const LINE = { border: "1px solid", borderColor: "divider", borderRadius: 1 };
const DIVIDED = { "& + &": { borderTop: "1px solid", borderColor: "divider" } };

/**
 * Review refresh (data.md step 5): a Knowledge base source has a newer
 * revision. Each change is accepted or rejected on its own, with the
 * scenarios it touches named; then Validate and save makes a new environment
 * version. A failed validation keeps the revision already pinned, says why,
 * lists the failing rows and offers a way to fix them. Keep pinned leaves
 * everything as it is.
 */
export default function ReviewRefreshDialog({
  open,
  onClose,
  env,
  envState,
  patch,
  sourceId,
}) {
  const theme = useTheme();
  const source = (envState.sources || []).find((s) => s.id === sourceId);
  const plan = useMemo(
    () => (source ? kbRefreshPlanOf(envState, source) : null),
    [envState, source],
  );
  const [decisions, setDecisions] = useState({});
  const [result, setResult] = useState(null);
  const [checking, setChecking] = useState(false);
  const [anchor, setAnchor] = useState(null);
  if (!source || !plan) return null;

  const accepted = plan.changes
    .filter((c) => (decisions[c.id] || "accept") === "accept")
    .map((c) => c.id);
  const touched = [
    ...new Map(
      plan.changes
        .filter((c) => accepted.includes(c.id))
        .flatMap((c) => c.scenarios)
        .map((s) => [s.id, s]),
    ).values(),
  ];
  const next = nextSnapshotLabel(env, envState);
  const decide = (id, v) => {
    if (!v) return;
    setDecisions((d) => ({ ...d, [id]: v }));
    setResult(null);
  };
  const close = () => {
    setResult(null);
    setDecisions({});
    onClose();
  };

  const validate = () => {
    setChecking(true);
    setTimeout(() => {
      setChecking(false);
      const r = validateKbRefresh(envState, plan, accepted);
      if (!r.ok) {
        setResult(r);
        return;
      }
      const saved = kbRefreshPatch(env, envState, source, plan, accepted);
      patch(saved);
      enqueueSnackbar(
        `Saved as environment ${saved.activeEnvVersion} with ${source.name} rev ${plan.latest}.`,
        { variant: "success" },
      );
      close();
    }, 500);
  };

  const keepPinned = () => {
    patch({
      sources: (envState.sources || []).map((s) =>
        s.id === source.id
          ? { ...s, kb: { ...s.kb, keptRev: plan.latest } }
          : s,
      ),
    });
    enqueueSnackbar(
      `Kept ${source.name} on rev ${plan.from}. Rev ${plan.latest} stays available to review.`,
      { variant: "info" },
    );
    close();
  };

  /* The fix path for rows that point at missing records: add the file. */
  const supplied = (read, table) => {
    patch({
      sources: [
        ...(envState.sources || []),
        ...read.map((r) => ({ ...r, fills: [table] })),
      ],
    });
    setResult(null);
    enqueueSnackbar(
      `Added ${read.map((r) => r.name).join(", ")} to the draft. Validate again.`,
      { variant: "success" },
    );
  };
  const failureTable = result?.failures?.find((f) => f.table)?.table;

  const kinds = [
    ["rules", "Rules"],
    ["rows", "Rows"],
  ];

  return (
    <SideDrawer open={open} onClose={close} width={560}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{
            px: 2.5,
            pt: 2,
            pb: 1.25,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
              {`Review refresh · ${source.name}`}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {/* Why it's open and what to do (#100): a new revision in the
                  Knowledge base; every change is in unless rejected. */}
              {`New in the Knowledge base: rev ${plan.from} → rev ${plan.latest} · ${plan.changes.length} ${plan.changes.length === 1 ? "change" : "changes"}, each accepted unless you reject it · then Save as ${next}`}
            </Typography>
          </Box>
          <IconButton size="small" onClick={close} aria-label="Close">
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
          {kinds.map(([group, label]) => {
            const list = plan.changes.filter(
              (c) => CHANGE_KIND[c.kind].group === group,
            );
            if (!list.length) return null;
            return (
              <Box key={group}>
                <Typography
                  sx={{
                    typography: "s3",
                    fontWeight: 600,
                    color: "text.tertiary",
                    px: 2.5,
                    pt: 1,
                    pb: 0.25,
                  }}
                >{`${label} · ${list.length}`}</Typography>
                <Box>
                  {list.map((c) => {
                    const on = (decisions[c.id] || "accept") === "accept";
                    return (
                      <Stack
                        key={c.id}
                        direction="row"
                        spacing={1.5}
                        sx={{ px: 2.5, py: 1, ...DIVIDED }}
                      >
                        <Box sx={{ flex: 1, minWidth: 0 }}>
                          <Typography
                            sx={{ typography: "s2", fontWeight: 600 }}
                          >
                            {c.label}
                            <Box
                              component="span"
                              sx={{ fontWeight: 400, color: "text.tertiary" }}
                            >
                              {` · ${CHANGE_KIND[c.kind].label.toLowerCase()}${c.ref ? ` · ${c.ref}` : ""}`}
                            </Box>
                          </Typography>
                          {c.from && (
                            <Typography
                              sx={{
                                typography: "s3",
                                color: "text.tertiary",
                                textDecoration: "line-through",
                                mt: 0.25,
                              }}
                            >
                              {c.from}
                            </Typography>
                          )}
                          {c.to && (
                            <Typography sx={{ typography: "s3", mt: 0.25 }}>
                              {c.to}
                            </Typography>
                          )}
                          {c.detail && !c.to && (
                            <Typography
                              sx={{ typography: "s3", color: "text.secondary" }}
                            >
                              {c.detail}
                            </Typography>
                          )}
                          {c.scenarios.length > 0 && (
                            <ScenarioCount list={c.scenarios} />
                          )}
                        </Box>
                        <ToggleButtonGroup
                          size="small"
                          exclusive
                          value={on ? "accept" : "reject"}
                          onChange={(_, v) => decide(c.id, v)}
                          aria-label={`${c.label}: accept or reject`}
                          sx={{
                            ...segmentedToggleSx(theme),
                            alignSelf: "flex-start",
                            flexShrink: 0,
                          }}
                        >
                          <ToggleButton value="accept">Accept</ToggleButton>
                          <ToggleButton value="reject">Reject</ToggleButton>
                        </ToggleButtonGroup>
                      </Stack>
                    );
                  })}
                </Box>
              </Box>
            );
          })}

          {result && !result.ok && (
            <Box
              ref={(el) => el && el.scrollIntoView({ block: "nearest" })}
              sx={{ ...LINE, p: 1.25, mx: 2.5, my: 1.5 }}
            >
              <Typography
                sx={{
                  typography: "s2",
                  fontWeight: 600,
                  color: "status.warning.fg",
                }}
              >
                {`Validation failed — rev ${plan.from} stays pinned`}
              </Typography>
              {result.failures.map((f) => (
                <Box key={f.id} sx={{ mt: 0.75 }}>
                  <Typography sx={{ typography: "s2" }}>
                    {f.change ? `${f.change.label}: ${f.reason}` : f.reason}
                  </Typography>
                  {f.rows.length > 0 && (
                    <Box sx={{ ...LINE, mt: 0.5 }}>
                      {f.rows.map((r) => (
                        <Typography
                          key={r.row}
                          sx={{
                            typography: "s3",
                            px: 1.25,
                            py: 0.5,
                            bgcolor: "action.selected",
                            "& + &": {
                              borderTop: "1px solid",
                              borderColor: "divider",
                            },
                          }}
                        >{`Row ${r.row}: ${r.reason}`}</Typography>
                      ))}
                    </Box>
                  )}
                </Box>
              ))}
              <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                {result.failures.some((f) => f.fix === "rows") && (
                  <>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={() => {
                        setDecisions((d) => ({
                          ...d,
                          ...Object.fromEntries(
                            result.failures
                              .filter((f) => f.change)
                              .map((f) => [f.change.id, "reject"]),
                          ),
                        }));
                        setResult(null);
                      }}
                    >
                      Reject those rows
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      onClick={(e) => setAnchor(e.currentTarget)}
                    >
                      Supply the records
                    </Button>
                  </>
                )}
              </Stack>
            </Box>
          )}
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            /* Clear of the floating assistant button in the corner. */
            pr: 9,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={1.5} alignItems="center" flex={1}>
            <Box
              component="span"
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {`${accepted.length} of ${plan.changes.length} accepted`}
            </Box>
            {touched.length > 0 && <ScenarioCount list={touched} />}
          </Stack>
          <Button size="small" variant="outlined" onClick={keepPinned}>
            {`Keep rev ${plan.from} pinned`}
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            disabled={checking}
            onClick={validate}
          >
            {checking ? "Validating…" : `Save as ${next}`}
          </Button>
        </Stack>
        <AddSourceMenu
          anchorEl={anchor}
          onClose={() => setAnchor(null)}
          onAddFiles={(files) => {
            const read = files
              .map(readFile)
              .filter((r) => r.status !== "unsupported");
            if (read.length) supplied(read, failureTable);
            else
              enqueueSnackbar("That file can't be read.", {
                variant: "warning",
              });
          }}
          onAddOrg={(o) => {
            if (!o.access) {
              enqueueSnackbar(
                `You don't have access to ${o.name}. Asked the ${o.owner} for it — you'll get a notification.`,
                { variant: "info" },
              );
              return;
            }
            supplied([sourceFromOrg(o)], failureTable);
          }}
          added={(envState.sources || [])
            .filter((s) => s.kb && !s.archived)
            .map((s) => s.name)}
          title={`Add the file that holds ${failureTable || "the missing records"}.`}
        />
      </Stack>
    </SideDrawer>
  );
}

/* "Affects 7 scenarios" — the names on hover, so the list doesn't crowd the review. */
function ScenarioCount({ list }) {
  return (
    <Tooltip
      arrow
      title={list.map((sc) => sc.name).join(" · ")}
      slotProps={{ tooltip: { sx: { maxWidth: 420 } } }}
    >
      <Typography
        component="span"
        sx={{
          typography: "s3",
          color: "text.tertiary",
          textDecoration: "underline dotted",
          cursor: "default",
        }}
      >
        {`Affects ${list.length} scenario${list.length === 1 ? "" : "s"}`}
      </Typography>
    </Tooltip>
  );
}
ScenarioCount.propTypes = { list: PropTypes.array.isRequired };

ReviewRefreshDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func.isRequired,
  sourceId: PropTypes.string,
};
