import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import SideDrawer from "../../components/SideDrawer";
import CopyCommandButton from "../../components/CopyCommandButton";
import {
  ROLES,
  VISIBILITY,
  PRIVACY,
  roleLabel,
  visibilityLabel,
  joinsOf,
  participantViewsOf,
  usageOf,
  refreshPlanOf,
  regroundPatch,
  keepPinnedPatch,
  exportPreviewOf,
  sourceEditPatch,
  dataCli,
} from "../../_mock/orgData";
import { nextSnapshotLabel } from "../../_mock/envSnapshot";

const MONO = "ui-monospace, Menlo, monospace";

/* The sensitive-value preview: what a value becomes under each choice. */
const shown = (value, mode, i = 0) => {
  if (mode === "omit") return "—";
  if (mode === "mask") return String(value).replace(/[^@.\s-]/g, "•");
  if (mode === "replace")
    return /@/.test(String(value))
      ? `customer${i + 1}@sandbox.test`
      : /^[A-Z][a-z]+ [A-Z]/.test(String(value))
        ? `Customer ${i + 1}`
        : `X-${100 + i}`;
  return String(value);
};

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
  readOnly = false,
}) {
  const source = (envState.sources || []).find((s) => s.id === sourceId);
  const [tab, setTab] = useState("preview");
  const [plan, setPlan] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const fileRef = useRef(null);
  if (!source) return <SideDrawer open={false} onClose={onClose} />;
  const p = source.parsed || {};
  const all = envState.sources || [];
  const edit = (fields, detail) =>
    patch(sourceEditPatch(envState, source.id, fields, detail));
  const usage = usageOf(source, envState);
  const views = participantViewsOf([source]);
  const joins = joinsOf(source, all);
  const exp = exportPreviewOf(source);
  const nextLabel = nextSnapshotLabel(env, envState);

  const TABS = [
    ["preview", "What was read"],
    ...(p.kind === "table"
      ? [
          ["mapping", "Mapping"],
          ["privacy", "Sensitive values"],
        ]
      : []),
    ["access", "Who sees it"],
    ["usage", "Used by"],
    ["versions", "Versions"],
  ];

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
                `v${source.version}`,
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

        {/* Role and visibility, always in view: the two choices that decide who reads it. */}
        <Stack direction="row" spacing={1} sx={{ px: 2.5, pb: 1.25 }}>
          <Select
            size="small"
            value={source.role || ""}
            disabled={readOnly || source.status === "unsupported"}
            onChange={(e) =>
              edit(
                { role: e.target.value },
                `${source.name} is now ${roleLabel(e.target.value)}`,
              )
            }
            sx={{ typography: "s2", minWidth: 200 }}
          >
            {ROLES.map((r) => (
              <MenuItem key={r.id} value={r.id} sx={{ typography: "s2" }}>
                {r.label}
              </MenuItem>
            ))}
          </Select>
          <Select
            size="small"
            value={source.visibility || ""}
            disabled={readOnly || source.status === "unsupported"}
            onChange={(e) =>
              edit(
                { visibility: e.target.value },
                `${source.name} is now ${visibilityLabel(e.target.value)}`,
              )
            }
            sx={{ typography: "s2", minWidth: 160 }}
          >
            {VISIBILITY.map((v) => (
              <MenuItem key={v.id} value={v.id} sx={{ typography: "s2" }}>
                {v.label}
              </MenuItem>
            ))}
          </Select>
        </Stack>

        <Tabs
          value={tab}
          onChange={(_, v) => setTab(v)}
          variant="scrollable"
          sx={{
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
              <Stack spacing={1}>
                {p.passages.map((x) => (
                  <Box
                    key={x.id}
                    sx={{
                      p: 1.25,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: 1,
                    }}
                  >
                    <Typography
                      sx={{ typography: "s3", color: "text.tertiary" }}
                    >{`Page ${x.page}${x.rule ? " · cited by a rule" : ""}`}</Typography>
                    <Typography sx={{ typography: "s2" }}>{x.text}</Typography>
                  </Box>
                ))}
                {source.effectiveFrom && (
                  <Typography
                    sx={{ typography: "s3", color: "text.tertiary" }}
                  >{`Effective from ${source.effectiveFrom}. Instructions inside a document are read as content, never as permission to change the workspace.`}</Typography>
                )}
              </Stack>
            ) : (
              <Stack spacing={1}>
                <Typography
                  sx={{ typography: "s3", color: "text.tertiary" }}
                >{`${p.format} · ${p.accepted.toLocaleString()} of ${p.rows.toLocaleString()} rows accepted`}</Typography>
                <Box
                  sx={{
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
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
                      sx={{ mt: 0.5, color: "text.secondary" }}
                    >
                      Download error report
                    </Button>
                  </Box>
                )}
              </Stack>
            ))}

          {tab === "mapping" && (
            <Stack spacing={1.5}>
              <Box>
                <KV label="Becomes table">
                  <Typography sx={{ typography: "s2", fontFamily: MONO }}>
                    {p.table}
                  </Typography>
                </KV>
                <KV label="Key">{p.columns.find((c) => c.key)?.name}</KV>
                <KV label="Duplicates">
                  Rejected — a later row with the same key is listed, not merged
                </KV>
                <KV label="Rows used">All permitted rows · no sample</KV>
              </Box>
              <Typography sx={{ typography: "s2", fontWeight: 600 }}>
                Relationships
              </Typography>
              {joins.length === 0 && (
                <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                  No references to other tables.
                </Typography>
              )}
              {joins.map((j) => (
                <Box
                  key={j.field}
                  sx={{
                    p: 1.25,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: 1,
                  }}
                >
                  <Typography sx={{ typography: "s2" }}>
                    <Box
                      component="span"
                      sx={{ fontFamily: MONO }}
                    >{`${p.table}.${j.field} → ${j.to}`}</Box>
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: j.blocking ? "status.warning.fg" : "text.tertiary",
                    }}
                  >
                    {j.resolution === "omit"
                      ? `${j.missing} rows left out — the cases that need them stay blocked.`
                      : !j.present
                        ? `${j.to.split(".")[0]} isn't attached — all ${j.missing} rows refer to it.`
                        : j.missing
                          ? `${j.missing} rows point at records that aren't there.`
                          : "Every row resolves."}
                  </Typography>
                  {j.blocking && !readOnly && (
                    <Stack direction="row" spacing={1} sx={{ mt: 0.75 }}>
                      <Button
                        size="small"
                        variant="outlined"
                        sx={{ color: "text.primary", borderColor: "divider" }}
                        onClick={() =>
                          enqueueSnackbar(
                            "Attach the missing file in builder chat — it's matched on the key automatically.",
                            { variant: "info" },
                          )
                        }
                      >
                        Supply the records
                      </Button>
                      <Button
                        size="small"
                        onClick={() =>
                          edit(
                            {
                              joins: {
                                ...(source.joins || {}),
                                [j.field]: "omit",
                              },
                            },
                            `${source.name}: rows without ${j.to.split(".")[0]} left out`,
                          )
                        }
                        sx={{ color: "text.secondary" }}
                      >
                        Leave those rows out
                      </Button>
                    </Stack>
                  )}
                </Box>
              ))}
            </Stack>
          )}

          {tab === "privacy" && (
            <Stack spacing={1}>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                Replace consistently keeps joins: the same customer gets the
                same stand-in across orders, refunds and conversations.
              </Typography>
              {p.columns.map((c) => {
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

          {tab === "access" && (
            <Box>
              {[
                ["agent", "Agent under test"],
                ["caller", "Simulated caller"],
                ["evaluator", "Evaluator"],
              ].map(([k, label]) => (
                <KV key={k} label={label}>
                  {views[k].length ? (
                    views[k][0].how
                  ) : (
                    <Box component="span" sx={{ color: "text.tertiary" }}>
                      Can&apos;t read it
                    </Box>
                  )}
                </KV>
              ))}
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mt: 1 }}
              >
                {source.visibility === "evaluator"
                  ? "Coming in through the same chat as other files doesn't give the agent access — it never reaches its context or tools."
                  : "Change who reads it with the visibility picker above."}
              </Typography>
            </Box>
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
                  size="small"
                  onClick={() => setArchiving(true)}
                  sx={{ mt: 1.5, color: "text.secondary" }}
                >
                  Archive this source
                </Button>
              )}
              {/* Not saved into the world yet: nothing depends on it, so it just goes. */}
              {!readOnly && !source.inEnv && (
                <Button
                  size="small"
                  onClick={() => {
                    patch({
                      sources: (envState.sources || []).filter(
                        (s) => s.id !== source.id,
                      ),
                    });
                    onClose();
                  }}
                  sx={{ mt: 1.5, color: "text.secondary" }}
                >
                  Remove — it isn&apos;t in the world yet
                </Button>
              )}
            </Box>
          )}

          {tab === "versions" && (
            <Stack spacing={1.5}>
              {[...source.versions].reverse().map((v) => (
                <KV
                  key={v.v}
                  label={`v${v.v}${v.v === source.version ? " · in use" : ""}`}
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
                        >{`Page ${c.page}`}</Typography>
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
                      {`Re-ground and save as ${nextLabel}`}
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
          <CopyCommandButton
            command={
              tab === "versions"
                ? dataCli.refresh(source)
                : dataCli.show(source)
            }
          />
          <Box flex={1} />
          <Button
            size="small"
            variant="outlined"
            onClick={() => setExporting(true)}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            Export
          </Button>
          {!readOnly && source.inEnv && source.parsed?.kind === "text" && (
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
                accept=".pdf,.txt,.md"
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
        open={exporting}
        onClose={() => setExporting(false)}
        title={
          exp.allowed
            ? `Export ${source.name} v${source.version}?`
            : "This source can't be exported"
        }
        content={
          exp.allowed
            ? [
                exp.masked.length &&
                  `Masked or replaced: ${exp.masked.join(", ")}.`,
                exp.left.length && `Left out: ${exp.left.join(", ")}.`,
                "Restricted and evaluator-only material isn't included.",
              ]
                .filter(Boolean)
                .join(" ")
            : exp.reason
        }
        action={
          exp.allowed && (
            <Button
              size="small"
              variant="contained"
              color="primary"
              onClick={() => {
                setExporting(false);
                enqueueSnackbar(
                  `Exported ${source.name} v${source.version}${exp.masked.length ? ` with ${exp.masked.length} fields masked` : ""}.`,
                  { variant: "success" },
                );
              }}
            >
              Export
            </Button>
          )
        }
      />
      <ConfirmDialog
        open={archiving}
        onClose={() => setArchiving(false)}
        title={`Archive ${source.name}?`}
        content={`New versions won't use it. ${usage.rules.length + usage.scenarios.length + usage.tables.length ? `It's used by ${[usage.rules.length && `${usage.rules.length} rules`, usage.scenarios.length && `${usage.scenarios.length} scenarios`, usage.tables.length && `${usage.tables.length} seed tables`].filter(Boolean).join(", ")} — they keep citing it on the versions they were made on. ` : ""}Past runs still show it.`}
        action={
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={() => {
              setArchiving(false);
              edit({ archived: true }, `${source.name} archived`);
              onClose();
            }}
          >
            Archive
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
  readOnly: PropTypes.bool,
};
