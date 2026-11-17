import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  Box,
  Button,
  ButtonBase,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { closeSnackbar, enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import SideDrawer from "../../components/SideDrawer";
import MappingEditor from "./MappingEditor";
import AddSourceMenu from "./AddSourceMenu";
import PersonalDataChoice from "./PersonalDataChoice";
import SourceUsage from "./SourceUsage";
import {
  normalizeRole,
  defaultVisibility,
  PRIVACY,
  SUPPORTED_ACCEPT,
  roleLabel,
  visibilityLabel,
  typeLabelOf,
  readFile,
  sourceFromOrg,
  usageOf,
  refreshPlanOf,
  regroundPatch,
  keepPinnedPatch,
  sourceEditPatch,
  placeLabelOf,
} from "../../_mock/orgData";
import {
  mappingToReview,
  personalChoicePatch,
  personalDataOf,
  standInOf,
  supplyRecordsPatch,
} from "../../_mock/dataMapping";
import { kbRefreshOf } from "../../_mock/kbCatalog";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";

const MONO = "ui-monospace, Menlo, monospace";

/* The sensitive-value preview: what a value becomes under each choice. */
const shown = standInOf;

function KV({ label, children }) {
  return (
    <Stack
      direction="row"
      spacing={2}
      sx={{
        py: 0.75,
        "& + &": { borderTop: "1px solid", borderColor: "divider" },
      }}
    >
      <Typography
        sx={{
          typography: "s3",
          color: "text.tertiary",
          width: 128,
          flexShrink: 0,
        }}
      >
        {label}
      </Typography>
      <Box sx={{ flex: 1, minWidth: 0, typography: "s2" }}>{children}</Box>
    </Stack>
  );
}
KV.propTypes = { label: PropTypes.string, children: PropTypes.node };

/**
 * One source (roadmap data.md §2–3): what was read, how it maps into the
 * world, how its sensitive values are handled, who can see it, what uses
 * it, and its versions — with Refresh (an impact review before anything
 * changes) and Export (only what's permitted).
 */
export default function SourceDrawer({
  open,
  onClose,
  env,
  envState,
  patch,
  sourceId,
  focusAt = null,
  focusTab = null,
  readOnly = false,
  onReviewRefresh = null,
  onReconnect = null,
}) {
  const source = (envState.sources || []).find((s) => s.id === sourceId);
  /* How it's used: two summary lines until Change opens the options. */
  const [usageOpen, setUsageOpen] = useState(false);
  /* The file's full text, folded until asked for (or a citation opens it). */
  const [fullText, setFullText] = useState(false);
  const [allLines, setAllLines] = useState(false);
  /* Each source opens folded — the drawer stays mounted between them. */
  useEffect(() => {
    setFullText(false);
    setAllLines(false);
    setUsageOpen(false);
  }, [sourceId]);
  const [tabState, setTab] = useState("preview");
  const [plan, setPlan] = useState(null);
  const [archiving, setArchiving] = useState(false);
  const [removing, setRemoving] = useState(false);
  /* Supplying missing records opens the same add-source menu, from the button. */
  const [supply, setSupply] = useState(null);
  const fileRef = useRef(null);
  /* Undo from a snackbar acts on the state as it is then, not as it was. */
  const latest = useRef(envState);
  latest.current = envState;
  /* Opened from a change in Review changes: show what was read, at the
     passage or row the change came from. */
  useEffect(() => {
    if (focusAt) setTab("preview");
  }, [sourceId, focusAt]);
  useEffect(() => {
    if (!focusTab) return;
    /* Remove from the Sources row (#96): the Usage tab, where removing
       sits, with its confirmation already open. */
    if (focusTab === "remove") {
      setTab("usage");
      const s = (envState.sources || []).find((x) => x.id === sourceId);
      if (s?.inEnv) setArchiving(true);
      else setRemoving(true);
      return;
    }
    setTab(focusTab);
  }, [sourceId, focusTab]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!source) return <SideDrawer open={false} onClose={onClose} />;
  const p = source.parsed || {};
  const all = envState.sources || [];
  const edit = (fields, detail) =>
    patch(sourceEditPatch(envState, source.id, fields, detail));
  /* Leaving rows out can be taken back: from the snackbar, or in Mapping. */
  const omitRows = (j) => {
    const table = j.to.split(".")[0];
    edit(
      { joins: { ...(source.joins || {}), [j.field]: "omit" } },
      `${source.name}: rows without ${table} left out`,
    );
    enqueueSnackbar(`Left ${j.missing} rows of ${source.name} out.`, {
      variant: "info",
      action: (key) => (
        <Button
          size="small"
          color="inherit"
          onClick={() => {
            closeSnackbar(key);
            const now = latest.current;
            const cur = (now.sources || []).find((x) => x.id === source.id);
            patch(
              sourceEditPatch(
                now,
                source.id,
                { joins: { ...(cur?.joins || {}), [j.field]: null } },
                `${source.name}: rows without ${table} put back`,
              ),
            );
          }}
        >
          Undo
        </Button>
      ),
    });
  };
  /* Records added to fill a gap are matched on the key straight away. */
  const supplyFiles = (files, join) => {
    const read = files.map(readFile);
    const usable = read.filter((r) => r.status !== "unsupported");
    if (usable.length)
      patch(supplyRecordsPatch(envState, source.id, join, usable));
    enqueueSnackbar(
      usable.length
        ? `Added ${usable.map((r) => r.name).join(", ")} to the draft. Nothing reaches the world until you accept it.`
        : read[0]?.statusNote || "That file can't be read.",
      { variant: usable.length ? "success" : "warning" },
    );
  };
  const supplyOrg = (o) => {
    if (!o.access) {
      enqueueSnackbar(
        `You don't have access to ${o.name}. Asked the ${o.owner} for it — you'll get a notification.`,
        { variant: "info" },
      );
      return;
    }
    patch(
      supplyRecordsPatch(envState, source.id, supply.join, [sourceFromOrg(o)]),
    );
    enqueueSnackbar(
      `Copied ${o.name} rev ${o.revision} into the draft. Nothing reaches the world until you accept it.`,
      { variant: "success" },
    );
  };
  const usage = usageOf(source, envState);
  const pd = personalDataOf(source);
  const refresh = kbRefreshOf(source);
  const toReview = mappingToReview(source);
  const nextLabel = nextSnapshotLabel(env, envState);
  /* The sample row is the one a change cited (its key value). */
  const focusRow =
    !!focusAt &&
    p.kind === "table" &&
    Object.values(p.sample || {}).includes(focusAt);

  const TABS = [
    ["preview", "What was read"],
    ...(p.kind === "table"
      ? [["mapping", toReview ? `Mapping · ${toReview} to review` : "Mapping"]]
      : []),
    ...(pd.has ? [["privacy", "Sensitive values"]] : []),
    ["usage", "Used by"],
    ["versions", "Versions"],
  ];
  /* The drawer stays mounted between sources, so a tab only a table has
     (Mapping, Sensitive values) falls back to What was read for a file. */
  const tab = TABS.some(([id]) => id === tabState) ? tabState : "preview";

  return (
    <SideDrawer open={open} onClose={onClose} width={560}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.5}
          sx={{ px: 2.5, pt: 2, pb: 1.25 }}
        >
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography noWrap sx={{ typography: "m2", fontWeight: 600 }}>
              {source.name}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {[
                typeLabelOf(source),
                source.kb ? `rev ${source.kb.revision}` : `v${source.version}`,
                source.owner,
                source.snapshot
                  ? `in snapshot ${source.snapshot}`
                  : "not in the environment yet",
                source.statusNote,
              ]
                .filter(Boolean)
                .join(" · ")}
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        {/* How the file is used and whether the agent reads it — asked as
            plain questions, every option in view. */}
        <Box sx={{ px: 2.5, pb: 1.5 }}>
          <SourceUsage
            compact={!usageOpen}
            onToggle={() => setUsageOpen((v) => !v)}
            role={normalizeRole(source.role)}
            visibility={source.visibility}
            disabled={readOnly || source.status === "unsupported"}
            onRole={(id) =>
              edit(
                { role: id, visibility: defaultVisibility(id) },
                `${source.name} is now ${roleLabel(id)}`,
              )
            }
            onVisibility={(id) =>
              edit(
                { visibility: id },
                `${source.name} is now ${visibilityLabel(id)}`,
              )
            }
          />
        </Box>
        {source.revoked && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 2.5, pb: 1 }}
          >
            <Typography
              sx={{ typography: "s3", color: "status.warning.fg", flex: 1 }}
            >
              {`Access revoked · keeping pinned rev ${source.kb?.revision} · refresh is blocked`}
            </Typography>
            {!readOnly && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onReconnect?.(source)}
              >
                Reconnect
              </Button>
            )}
          </Stack>
        )}

        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          variant="scrollable"
          sx={{
            boxShadow: "none",
            px: 2.5,
            borderBottom: "1px solid",
            borderColor: "divider",
            minHeight: 36,
          }}
        >
          {TABS.map(([id, label]) => (
            <Tab
              key={id}
              value={id}
              label={label}
              sx={{ minHeight: 36, typography: "s2" }}
            />
          ))}
        </Tabs>

        <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 1.5 }}>
          {tab === "preview" &&
            (source.status === "unreadable" ||
            source.status === "unsupported" ? (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {source.statusNote}
              </Typography>
            ) : p.kind === "text" ? (
              <Stack spacing={1.5}>
                {/* What the builder took from the file comes first (data.md §2:
                    "show which policy statements become scenario rules"); the
                    full text stays folded unless asked for or cited. */}
                {(() => {
                  const taken = p.passages.filter((x) => x.rule);
                  const showFull = fullText || !!focusAt;
                  const paged = ["pdf", "docx"].includes(source.ext);
                  const units = new Set(p.passages.map((x) => x.page)).size;
                  const rows = showFull
                    ? allLines
                      ? p.passages
                      : p.passages.slice(0, 20)
                    : [];
                  const line = (x, n, list) => {
                    const first = n === 0 || list[n - 1].page !== x.page;
                    const lit = x.id === focusAt;
                    const cell = {
                      py: 0.75,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                      ...(lit && { bgcolor: "action.selected" }),
                    };
                    return [
                      <Typography
                        key={`${x.id}-w`}
                        sx={{
                          ...cell,
                          typography: "s3",
                          color: "text.tertiary",
                          pr: 1,
                        }}
                      >
                        {first ? placeLabelOf(source, x.page) : ""}
                      </Typography>,
                      <Box
                        key={`${x.id}-t`}
                        ref={(el) =>
                          el && lit && el.scrollIntoView({ block: "center" })
                        }
                        sx={cell}
                      >
                        <Typography sx={{ typography: "s1" }}>
                          {x.text}
                        </Typography>
                        {lit && (
                          <Typography
                            sx={{ typography: "s3", color: "text.tertiary" }}
                          >
                            The change came from here
                          </Typography>
                        )}
                      </Box>,
                    ];
                  };
                  const grid = (list) => (
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: "72px minmax(0, 1fr)",
                        borderTop: "1px solid",
                        borderColor: "divider",
                      }}
                    >
                      {list.map((x, n) => line(x, n, list))}
                    </Box>
                  );
                  const link = (label, onClick) => (
                    <ButtonBase
                      onClick={onClick}
                      sx={{
                        alignSelf: "flex-start",
                        typography: "s1",
                        fontWeight: 500,
                        color: "text.secondary",
                        textDecoration: "underline",
                        textUnderlineOffset: 3,
                      }}
                    >
                      {label}
                    </ButtonBase>
                  );
                  return (
                    <>
                      <Box>
                        <Typography
                          sx={{ typography: "m3", fontWeight: 500, mb: 0.5 }}
                        >
                          What the builder took from it
                          <Box
                            component="span"
                            sx={{
                              color: "text.tertiary",
                              fontWeight: 400,
                              ml: 0.75,
                            }}
                          >
                            {taken.length}
                          </Box>
                        </Typography>
                        {taken.length ? (
                          grid(taken)
                        ) : (
                          <Typography
                            sx={{ typography: "s1", color: "text.tertiary" }}
                          >
                            {normalizeRole(source.role) === "policy"
                              ? "No sentence in it states a rule, so nothing is graded from it."
                              : "Nothing is graded from it — the agent reads it as background."}
                          </Typography>
                        )}
                      </Box>
                      <Box>
                        {showFull ? (
                          <>
                            <Typography
                              sx={{
                                typography: "m3",
                                fontWeight: 500,
                                mb: 0.5,
                              }}
                            >
                              Full text
                              <Box
                                component="span"
                                sx={{
                                  color: "text.tertiary",
                                  fontWeight: 400,
                                  ml: 0.75,
                                }}
                              >
                                {`${units} ${paged ? "pages" : "sections"} · ${p.passages.length} lines`}
                              </Box>
                            </Typography>
                            {grid(rows)}
                            <Stack
                              direction="row"
                              spacing={2}
                              sx={{ mt: 0.75 }}
                            >
                              {p.passages.length > 20 &&
                                link(
                                  allLines
                                    ? "Show fewer lines"
                                    : `Show all ${p.passages.length} lines`,
                                  () => setAllLines((v) => !v),
                                )}
                              {!focusAt &&
                                link("Hide the full text", () => {
                                  setFullText(false);
                                  setAllLines(false);
                                })}
                            </Stack>
                          </>
                        ) : (
                          link(
                            `Show the full text · ${units} ${paged ? "pages" : "sections"}, ${p.passages.length} lines`,
                            () => setFullText(true),
                          )
                        )}
                      </Box>
                    </>
                  );
                })()}
                {source.effectiveFrom && (
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >{`Effective from ${source.effectiveFrom}. Instructions inside a document are read as content only.`}</Typography>
                )}
              </Stack>
            ) : (
              <Stack spacing={1}>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >{`${p.format} · ${p.accepted.toLocaleString()} of ${p.rows.toLocaleString()} rows accepted`}</Typography>
                {focusRow && (
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {`Row ${focusAt} — the change came from this row`}
                  </Typography>
                )}
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                    ...(focusRow && { bgcolor: "action.selected" }),
                  }}
                >
                  {p.columns.map((c) => (
                    <Stack
                      key={c.name}
                      direction="row"
                      spacing={1.5}
                      sx={{
                        px: 1.25,
                        py: 0.75,
                        "& + &": {
                          borderTop: "1px solid",
                          borderColor: "divider",
                        },
                      }}
                    >
                      <Typography
                        sx={{
                          typography: "s2",
                          fontFamily: MONO,
                          width: 140,
                          flexShrink: 0,
                        }}
                      >
                        {c.name}
                      </Typography>
                      <Typography
                        sx={{
                          typography: "s3",
                          color: "text.tertiary",
                          width: 64,
                        }}
                      >
                        {c.type}
                      </Typography>
                      <Typography
                        noWrap
                        sx={{ typography: "s3", flex: 1, fontFamily: MONO }}
                      >
                        {String(p.sample?.[c.name] ?? "")}
                      </Typography>
                      <Typography
                        sx={{ typography: "s3", color: "text.tertiary" }}
                      >
                        {c.key
                          ? "key"
                          : c.ref
                            ? `→ ${c.ref}`
                            : c.sensitive
                              ? "sensitive"
                              : ""}
                      </Typography>
                    </Stack>
                  ))}
                </Box>
                {p.rejected.length > 0 && (
                  <Box>
                    <Typography
                      sx={{ typography: "s3", fontWeight: 600, mt: 1 }}
                    >{`Rejected rows · ${p.rejected.length}`}</Typography>
                    {p.rejected.map((r) => (
                      <Typography
                        key={r.row}
                        sx={{ typography: "s3", color: "text.secondary" }}
                      >{`Row ${r.row}: ${r.reason}`}</Typography>
                    ))}
                    <Button
                      variant="outlined"
                      size="small"
                      startIcon={
                        <Iconify
                          icon="solar:download-minimalistic-linear"
                          width={14}
                        />
                      }
                      onClick={() =>
                        enqueueSnackbar(
                          `Error report for ${source.name}: ${p.rejected.length} rows with reasons.`,
                          { variant: "success" },
                        )
                      }
                      sx={{ mt: 0.5 }}
                    >
                      Download error report
                    </Button>
                  </Box>
                )}
              </Stack>
            ))}

          {tab === "mapping" && (
            <MappingEditor
              source={source}
              envState={envState}
              patch={patch}
              readOnly={readOnly}
              onSupply={(anchor, join) => setSupply({ anchor, join })}
              onOmit={omitRows}
              onPutBack={(j) =>
                edit(
                  { joins: { ...(source.joins || {}), [j.field]: null } },
                  `${source.name}: rows without ${j.to.split(".")[0]} put back`,
                )
              }
            />
          )}

          {tab === "privacy" && (
            <Stack spacing={1.5}>
              <Box>
                <Typography sx={{ typography: "s2", fontWeight: 600, mb: 0.5 }}>
                  {pd.choice
                    ? "Personal data"
                    : "Personal data — choose how to handle it"}
                </Typography>
                <PersonalDataChoice
                  suggested={pd.suggested}
                  choice={pd.choice}
                  columns={pd.columns}
                  readOnly={readOnly}
                  onChoose={(c) =>
                    patch(personalChoicePatch(envState, source, c))
                  }
                />
              </Box>
              {p.kind === "table" && (
                <Stack spacing={1}>
                  <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                    Replace consistently keeps joins: the same customer gets the
                    same stand-in across orders, refunds and conversations.
                  </Typography>
                  {p.columns
                    .filter((c) => c.sensitive || c.stableId || c.key)
                    .map((c) => {
                      const mode = source.privacy?.[c.name] || "retain";
                      return (
                        <Stack
                          key={c.name}
                          direction="row"
                          alignItems="center"
                          spacing={1.5}
                          sx={{ py: 0.5 }}
                        >
                          <Typography
                            sx={{
                              typography: "s2",
                              fontFamily: MONO,
                              width: 120,
                              flexShrink: 0,
                            }}
                          >
                            {c.name}
                          </Typography>
                          <Select
                            size="small"
                            value={mode}
                            disabled={readOnly}
                            onChange={(e) =>
                              edit(
                                {
                                  privacy: {
                                    ...(source.privacy || {}),
                                    [c.name]: e.target.value,
                                  },
                                },
                                `${source.name}.${c.name}: ${PRIVACY.find((x) => x.id === e.target.value).label.toLowerCase()}`,
                              )
                            }
                            sx={{ typography: "s3", width: 180 }}
                          >
                            {PRIVACY.map((x) => (
                              <MenuItem
                                key={x.id}
                                value={x.id}
                                sx={{ typography: "s3" }}
                              >
                                {x.label}
                              </MenuItem>
                            ))}
                          </Select>
                          <Typography
                            noWrap
                            sx={{
                              typography: "s3",
                              fontFamily: MONO,
                              color: "text.secondary",
                              flex: 1,
                              minWidth: 0,
                            }}
                          >
                            {`${String(p.sample?.[c.name] ?? "")} → ${shown(p.sample?.[c.name] ?? "", mode)}`}
                          </Typography>
                        </Stack>
                      );
                    })}
                </Stack>
              )}
            </Stack>
          )}

          {tab === "usage" && (
            <Box>
              <KV label="Rules">
                {usage.rules.length
                  ? usage.rules.map((r) => `${r.id} ${r.to}`).join(" · ")
                  : "None"}
              </KV>
              <KV label="Scenarios">
                {usage.scenarios.length
                  ? `${usage.scenarios.length} cite it`
                  : "None yet"}
              </KV>
              <KV label="Seeds">
                {usage.tables.length
                  ? usage.tables
                      .map((t) => `${t.name} · ${t.rows.toLocaleString()} rows`)
                      .join(" · ")
                  : "Nothing"}
              </KV>
              {!readOnly && source.inEnv && (
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setArchiving(true)}
                  sx={{ mt: 1.5 }}
                >
                  Remove from this environment
                </Button>
              )}
              {/* Not saved into the world yet: nothing depends on it, so it just goes. */}
              {!readOnly && !source.inEnv && (
                <Button
                  variant="outlined"
                  size="small"
                  onClick={() => setRemoving(true)}
                  sx={{ mt: 1.5 }}
                >
                  Remove — it isn&apos;t in the world yet
                </Button>
              )}
            </Box>
          )}

          {tab === "versions" && (
            <Stack spacing={1.5}>
              {source.kb && (
                <Box>
                  <KV label="Knowledge base">
                    {`${source.kb.collection} · pinned rev ${source.kb.revision}${source.kb.leftOut?.length ? ` · ${source.kb.leftOut.length} ${source.kb.leftOut.length === 1 ? "change" : "changes"} left out` : ""}`}
                  </KV>
                  <KV label="Snapshot">
                    A copy of that revision. It isn&apos;t linked live — edits
                    in the Knowledge base don&apos;t change this environment.
                  </KV>
                  <KV label="Refresh">
                    {source.revoked
                      ? `Access revoked · keeping pinned rev ${source.kb.revision} · refresh is blocked`
                      : refresh
                        ? `New revision available · rev ${refresh.latest}${refresh.note ? ` — ${refresh.note}` : ""}`
                        : "Up to date. A new revision waits for you to review it; it is never pulled in on its own."}
                  </KV>
                  {(refresh || source.revoked) && !readOnly && (
                    <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
                      {refresh && (
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => onReviewRefresh?.(source)}
                        >
                          Review refresh
                        </Button>
                      )}
                      {source.revoked && (
                        <Button
                          size="small"
                          variant="outlined"
                          onClick={() => onReconnect?.(source)}
                        >
                          Reconnect
                        </Button>
                      )}
                    </Stack>
                  )}
                </Box>
              )}
              {[...source.versions].reverse().map((v) => (
                <KV
                  key={v.v}
                  label={`${v.rev ? `rev ${v.rev}` : `v${v.v}`}${v.v === source.version ? " · in use" : ""}`}
                >
                  {`${v.note}${v.inVersion ? ` · environment ${v.inVersion}` : ""}`}
                </KV>
              ))}
              {source.pendingRefresh && (
                <KV label={`v${source.pendingRefresh.v} · kept`}>
                  {`${source.pendingRefresh.name} — kept, not used. Runs still pin v${source.version}.`}
                </KV>
              )}
              {plan && (
                <Box
                  sx={{
                    p: 1.5,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  <Typography
                    sx={{ typography: "s2", fontWeight: 600 }}
                  >{`Replacing with ${plan.next.name}`}</Typography>
                  {plan.changed.length ? (
                    plan.changed.map((c) => (
                      <Box key={c.id} sx={{ mt: 0.75 }}>
                        <Typography
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {placeLabelOf(plan.next || source, c.page)}
                        </Typography>
                        <Typography
                          sx={{
                            typography: "s3",
                            textDecoration: "line-through",
                            color: "text.tertiary",
                          }}
                        >
                          {c.from}
                        </Typography>
                        <Typography sx={{ typography: "s3" }}>
                          {c.to}
                        </Typography>
                      </Box>
                    ))
                  ) : (
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >
                      No passage changed.
                    </Typography>
                  )}
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary", mt: 1 }}
                  >
                    {`${plan.rules.length} rule${plan.rules.length === 1 ? "" : "s"} and ${plan.affected.length} scenario${plan.affected.length === 1 ? "" : "s"} depend on what changed. Past runs keep v${source.version}; a run on the new version isn't the same test, so it isn't credited as an agent improvement.`}
                  </Typography>
                  <Stack direction="row" spacing={1} sx={{ mt: 1.25 }}>
                    <Button
                      size="small"
                      variant="outlined"
                      sx={{ color: "text.primary", borderColor: "divider" }}
                      onClick={() => {
                        patch(keepPinnedPatch(envState, source, plan));
                        setPlan(null);
                      }}
                    >
                      Keep v{source.version} pinned
                    </Button>
                    <Button
                      size="small"
                      variant="contained"
                      color="primary"
                      onClick={() => {
                        patch(regroundPatch(env, envState, source, plan));
                        setPlan(null);
                      }}
                    >
                      {`Move to the new version and save as ${nextLabel}`}
                    </Button>
                    <Button
                      size="small"
                      onClick={() => setPlan(null)}
                      sx={{ color: "text.secondary" }}
                    >
                      Cancel
                    </Button>
                  </Stack>
                </Box>
              )}
            </Stack>
          )}
        </Box>

        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {/* Replace (data.md §5, refresh) is the drawer's one action. Export
              and copy-as-command aren't in the plan for a source. */}
          <Box flex={1} />
          {!readOnly &&
            source.inEnv &&
            !source.revoked &&
            source.parsed?.kind === "text" && (
              <>
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => fileRef.current?.click()}
                  sx={{ color: "text.primary", borderColor: "divider" }}
                >
                  Replace with a new version
                </Button>
                <input
                  ref={fileRef}
                  hidden
                  type="file"
                  accept={SUPPORTED_ACCEPT}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) {
                      setPlan(refreshPlanOf(env, envState, source, f));
                      setTab("versions");
                    }
                    e.target.value = "";
                  }}
                />
              </>
            )}
        </Stack>
      </Stack>

      <ConfirmDialog
        open={removing}
        onClose={() => setRemoving(false)}
        title={`Remove ${source.name}?`}
        content="It isn't in the world yet. Its reading and mapping are discarded; add it again from Sources."
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              setRemoving(false);
              patch({
                sources: (envState.sources || []).filter(
                  (s) => s.id !== source.id,
                ),
              });
              onClose();
            }}
          >
            Remove
          </Button>
        }
      />
      <AddSourceMenu
        anchorEl={supply?.anchor}
        onClose={() => setSupply((c) => c && { ...c, anchor: null })}
        onAddFiles={(files) => supplyFiles(files, supply.join)}
        onAddOrg={supplyOrg}
        added={all.filter((x) => x.kb && !x.archived).map((x) => x.name)}
        title={`Add the file that holds ${supply?.join?.to?.split(".")[0] || "the missing records"}.`}
      />
      <ConfirmDialog
        open={archiving}
        onClose={() => setArchiving(false)}
        title={`Remove ${source.name} from this environment?`}
        content={`New versions won't use it. ${usage.rules.length + usage.scenarios.length + usage.tables.length ? `It's used by ${[usage.rules.length && `${usage.rules.length} rules`, usage.scenarios.length && `${usage.scenarios.length} scenarios`, usage.tables.length && `${usage.tables.length} seed tables`].filter(Boolean).join(", ")} — they keep citing it on the versions they were made on. ` : ""}Past runs still show it.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              setArchiving(false);
              edit({ archived: true }, `${source.name} removed`);
              onClose();
            }}
          >
            Remove
          </Button>
        }
      />
    </SideDrawer>
  );
}

SourceDrawer.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  env: PropTypes.object,
  envState: PropTypes.object,
  patch: PropTypes.func,
  sourceId: PropTypes.string,
  focusAt: PropTypes.string,
  focusTab: PropTypes.string,
  readOnly: PropTypes.bool,
  onReviewRefresh: PropTypes.func,
  onReconnect: PropTypes.func,
};
