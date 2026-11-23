import { readDesignFiles } from "../../_mock/designSources";
import PropTypes from "prop-types";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  ButtonBase,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { SectionCard } from "../../components/primitives";
import {
  participantViewsOf,
  readFile,
  sourceFromOrg,
  ROLES,
  normalizeRole,
  VISIBILITY,
  roleLabel,
  visibilityLabel,
  typeLabelOf,
  usageOf,
  dataCli,
  sourceEditPatch,
  defaultVisibility,
} from "../../_mock/orgData";
import { kbRefreshOf } from "../../_mock/kbCatalog";
import { personalDataOf } from "../../_mock/dataMapping";
import { seedSourcesPatch } from "../../_mock/orgSourceSeed";
import AddSourceMenu from "./AddSourceMenu";
import DataProposalCard from "./DataProposalCard";
import ReviewRefreshDialog from "./ReviewRefreshDialog";
import SourceDrawer from "./SourceDrawer";
import UploadRoles from "./UploadRoles";
import SideDrawer from "../../components/SideDrawer";

const STATUS = {
  ready: "Ready",
  "needs-attention": "Needs attention",
  unreadable: "Can't read",
  unsupported: "Not supported",
  "no-access": "No access",
};
const GRID = "minmax(180px, 2fr) 56px 1fr 1.1fr 1fr 80px 0.9fr 28px";
const ago = (iso) => {
  const h = Math.round((Date.now() - Date.parse(iso || "")) / 3600000);
  return h < 1
    ? "just now"
    : h < 24
      ? `${h} h ago`
      : `${Math.round(h / 24)} d ago`;
};

/* What a file is used by, by name: the scenarios, rules and tables that cite it. */
const USE_SHOWN = 8;
const usedByLinesOf = (u) =>
  [
    [
      "Scenarios",
      u.scenarios.map((r) => r.title || r.name || r.task || r.id),
    ],
    [
      "Rules",
      u.rules.map((r) => {
        const t = String(r.to || "");
        return `${r.id}${t ? `: ${t.length > 72 ? `${t.slice(0, 72)}…` : t}` : ""}`;
      }),
    ],
    ["Tables", u.tables.map((t) => t.name || t.table || t.label || t.id)],
  ].filter(([, names]) => names.length);

/**
 * World & tools → Sources (roadmap data.md "Manage evidence and data after
 * import"): every file and organization source this world is built from —
 * role, who can read it, state, version and what uses it — with who sees
 * what across all of them, and Add source (upload, or a workspace source
 * by reference). A row opens the source.
 */
export default function SourcesSection({
  env,
  envState,
  patch,
  readOnly = false,
}) {
  const [anchor, setAnchor] = useState(null);
  const [review, setReview] = useState(null);
  /* Files are read in a sandbox and take a moment each: until then a row
     says Reading and the review fills in file by file. */
  const [now, setNow] = useState(Date.now());
  const readingUntil = Math.max(
    0,
    ...(envState.sources || []).map((x) => x.readUntil || 0),
  );
  useEffect(() => {
    if (readingUntil <= Date.now()) return undefined;
    const t = setInterval(() => setNow(Date.now()), 250);
    const stop = setTimeout(
      () => {
        clearInterval(t);
        setNow(Date.now());
      },
      readingUntil - Date.now() + 300,
    );
    return () => {
      clearInterval(t);
      clearTimeout(stop);
    };
  }, [readingUntil]);
  const isReading = (x) => (x?.readUntil || 0) > now;
  /* A source whose Knowledge base file changed, being reviewed. */
  const [refreshing, setRefreshing] = useState(null);
  /* The row whose "Used by" is open, listing what uses the file by name. */
  const [usedOpen, setUsedOpen] = useState(null);
  /* The open source lives in the URL (?source=), so a citation in builder chat can open it. */
  const [params, setParams] = useSearchParams();
  const open = params.get("source");
  /* The place in it a change came from (a passage or a row), if any. */
  const openAt = params.get("at");
  const openTab = params.get("tab");
  const setOpen = (id, tab = null) =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        if (id) n.set("source", id);
        else n.delete("source");
        if (id && tab) n.set("tab", tab);
        else n.delete("tab");
        n.delete("at");
        return n;
      },
      { replace: true },
    );
  /* A seeded environment's sources arrive once, the first time they're opened. */
  useEffect(() => {
    if (!patch) return;
    const seed = seedSourcesPatch(env, envState);
    if (seed) patch(seed);
  }, [env, envState, patch]);
  const sources = (envState.sources || []).filter((s) => !s.archived);
  const archived = (envState.sources || []).filter((s) => s.archived);
  const inEnv = sources.filter((s) => s.inEnv);
  /* Added from a citation: in the world as evidence, its proposal not yet reviewed. */
  const pending = sources.filter((s) => s.suggestionsPending);
  const views = participantViewsOf(inEnv);
  const snapshot = inEnv.find((s) => s.snapshot)?.snapshot;
  const newest = inEnv
    .map((s) => s.versions[s.versions.length - 1]?.at)
    .sort()
    .pop();

  /* Before the first build, files join the design under review instead of
     proposing a next version. */
  const designing = env.buildStatus === "awaiting_approval";
  const addFiles = async (files) => {
    /* Text files are read for their words, not guessed from their name. */
    const at = Date.now();
    const read = (await readDesignFiles(files))
      .map(readFile)
      .map((x, i) => ({ ...x, readUntil: at + 1800 + i * 1400 }));
    patch({ sources: [...(envState.sources || []), ...read] });
    setNow(at);
    const usable = read.filter((s) => s.status !== "unsupported");
    if (usable.length && !designing) setReview(usable.map((s) => s.id));
  };
  const addOrg = (o) => {
    setAnchor(null);
    if (!o.access) {
      enqueueSnackbar(
        `You don't have access to ${o.name}. Asked the ${o.owner} for it — you'll get a notification.`,
        { variant: "info" },
      );
      return;
    }
    /* From the Knowledge base, pinned to the revision chosen: a newer
       revision is offered as a refresh to review, never applied on its own. */
    const s = { ...sourceFromOrg(o), readUntil: Date.now() + 1800 };
    patch({ sources: [...(envState.sources || []), s] });
    setNow(Date.now());
    if (!designing) setReview([s.id]);
  };

  return (
    <SectionCard
      title={`Sources · ${sources.length}`}
      subtitle={
        inEnv.length
          ? `${envState.dataMode === "private-sample" ? "Real records, a private sample" : "Synthetic data"} · snapshot ${snapshot || "—"} · updated ${ago(newest)}${sources.length > inEnv.length ? ` · ${sources.length - inEnv.length} not saved yet` : ""}`
          : sources.length
            ? `${sources.length} not saved yet — accept the builder's proposal to add ${sources.length === 1 ? "it" : "them"} to the world.`
            : "The files and data this world is built from — none yet. Attach one in builder chat, or add it here."
      }
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          {!readOnly && (
            <Button
              size="small"
              variant="outlined"
              onClick={(e) => setAnchor(e.currentTarget)}
              startIcon={<Iconify icon="solar:add-circle-linear" width={16} />}
              sx={{ color: "text.primary", borderColor: "divider" }}
            >
              Add source
            </Button>
          )}
        </Stack>
      }
      sx={{ mb: 2 }}
    >
      <AddSourceMenu
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        onAddFiles={addFiles}
        onAddOrg={addOrg}
        added={sources.filter((s) => s.kb).map((s) => s.name)}
        command={dataCli.import(env, sources[0])}
      />
      {!readOnly && pending.length > 0 && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{
            mb: 1.5,
            px: 1.5,
            py: 1,
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
          }}
        >
          <Iconify
            icon="solar:info-circle-linear"
            width={14}
            sx={{ color: "text.tertiary", flexShrink: 0 }}
          />
          <Typography
            sx={{ typography: "s1", color: "text.secondary", flex: 1 }}
          >
            {`${pending.map((x) => x.name).join(", ")} ${pending.length === 1 ? "was" : "were"} added as evidence on a scenario. What ${pending.length === 1 ? "it suggests" : "they suggest"} for rules and scenarios hasn't been reviewed.`}
          </Typography>
          <Button
            size="small"
            variant="outlined"
            onClick={() => {
              setReview(pending.map((x) => x.id));
              patch({
                sources: (envState.sources || []).map((x) =>
                  x.suggestionsPending
                    ? { ...x, suggestionsPending: false }
                    : x,
                ),
              });
            }}
          >
            Review suggestions
          </Button>
        </Stack>
      )}

      {sources.length > 0 && (
        <Box>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: GRID,
              gap: 1.5,
              px: 2,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {[
              "Source",
              "Type",
              "Used as",
              "Who reads it",
              "State",
              "Version",
              "Used by",
              "",
            ].map((h) => (
              <Typography
                key={h}
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {h}
              </Typography>
            ))}
          </Box>
          {sources.map((s) => {
            const u = usageOf(s, envState);
            const n = u.rules.length + u.scenarios.length + u.tables.length;
            const usesShown = usedOpen === s.id && n > 0;
            const refresh = kbRefreshOf(s);
            const pd = personalDataOf(s);
            const choosing = pd.has && !pd.choice;
            /* A file that needs something from you says what, and where to
               click, on its own line (#99) — not only in the State cell. */
            /* A file that needs something (#99): a short state that says
               what, the fix on hover; clicking the row opens it there. */
            const problem =
              s.status === "needs-attention"
                ? {
                    short:
                      s.statusNote?.replace(/ of \d+ rows/, " rows") ||
                      "Needs attention",
                    hint: "Open it to see which rows and why.",
                  }
                : s.status === "unreadable"
                  ? {
                      short: "Can't read",
                      hint:
                        s.statusNote ||
                        "No text to read — upload a text version instead.",
                    }
                  : s.status === "unsupported"
                    ? {
                        short: "Not supported",
                        hint: `${s.statusNote || "This file type can't be read."} Export it as PDF or DOCX and upload that.`,
                      }
                    : null;
            return (
              <Box
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => setOpen(s.id)}
                onKeyDown={(e) => e.key === "Enter" && setOpen(s.id)}
                sx={{
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Box
                  sx={{
                    display: "grid",
                    gridTemplateColumns: GRID,
                    gap: 1.5,
                    px: 2,
                    py: 0.875,
                  }}
                >
                  <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                    {s.name}
                  </Typography>
                  <Typography
                    sx={{ typography: "s2", color: "text.secondary" }}
                  >
                    {typeLabelOf(s)}
                  </Typography>
                  <Tooltip
                    arrow
                    title={
                      ROLES.find((r) => r.id === normalizeRole(s.role))?.note ||
                      ""
                    }
                  >
                    <Typography noWrap sx={{ typography: "s2" }}>
                      {s.role ? roleLabel(s.role) : "—"}
                    </Typography>
                  </Tooltip>
                  <Tooltip
                    arrow
                    title={
                      VISIBILITY.find((v) => v.id === s.visibility)?.note || ""
                    }
                  >
                    <Typography noWrap sx={{ typography: "s2" }}>
                      {s.visibility ? visibilityLabel(s.visibility) : "—"}
                    </Typography>
                  </Tooltip>
                  <Tooltip arrow title={problem?.hint || ""}>
                    <Typography
                      noWrap
                      sx={{
                        typography: "s2",
                        color:
                          s.status === "ready" && !s.revoked
                            ? "text.primary"
                            : "status.warning.fg",
                        ...(problem && {
                          textDecoration: "underline dotted",
                          textUnderlineOffset: "3px",
                        }),
                      }}
                    >
                      {isReading(s)
                        ? "Reading…"
                        : s.revoked
                          ? "Removed"
                          : problem
                            ? problem.short
                            : s.inEnv
                              ? STATUS[s.status]
                              : s.status === "ready"
                                ? "Not saved yet"
                                : STATUS[s.status]}
                    </Typography>
                  </Tooltip>
                  <Typography
                    sx={{
                      typography: "s2",
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {/* data.md §4: a KB source is a copied snapshot — said
                        on the row, so a later KB edit can't pass for it. */}
                    {`${s.kb ? "your copy" : `v${s.version}`}${s.pendingRefresh ? ` · v${s.pendingRefresh.v} kept` : ""}`}
                  </Typography>
                  {n ? (
                    <ButtonBase
                      aria-expanded={usesShown}
                      aria-label={`${usesShown ? "Hide" : "Show"} what uses ${s.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setUsedOpen((cur) => (cur === s.id ? null : s.id));
                      }}
                      onKeyDown={(e) => e.stopPropagation()}
                      sx={{
                        justifySelf: "start",
                        typography: "s2",
                        color: "text.primary",
                        gap: 0.25,
                        whiteSpace: "nowrap",
                        "&:hover": { color: "primary.main" },
                      }}
                    >
                      {`${n} item${n === 1 ? "" : "s"}`}
                      <Iconify
                        icon={
                          usesShown
                            ? "solar:alt-arrow-up-linear"
                            : "solar:alt-arrow-down-linear"
                        }
                        width={12}
                        sx={{ color: "text.tertiary" }}
                      />
                    </ButtonBase>
                  ) : (
                    <Typography
                      noWrap
                      sx={{ typography: "s2", color: "text.tertiary" }}
                    >
                      Nothing yet
                    </Typography>
                  )}
                  {/* Remove a file from its row (#96) — the confirmation
                      says what still uses it. */}
                  {!readOnly ? (
                    <Tooltip
                      arrow
                      title={
                        s.inEnv
                          ? "Remove from this environment — new versions stop using it; past runs keep it"
                          : "Remove — it isn't in the world yet"
                      }
                    >
                      <IconButton
                        size="small"
                        aria-label={`Remove ${s.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpen(s.id, "remove");
                        }}
                        sx={{ p: 0.25, color: "text.tertiary" }}
                      >
                        <Iconify
                          icon="solar:trash-bin-minimalistic-linear"
                          width={16}
                        />
                      </IconButton>
                    </Tooltip>
                  ) : (
                    <span />
                  )}
                </Box>
                {usesShown && (
                  <Box
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                    sx={{
                      mx: 2,
                      mb: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      borderRadius: "8px",
                      cursor: "default",
                    }}
                  >
                    {usedByLinesOf(u).map(([label, names]) => (
                      <Box
                        key={label}
                        sx={{
                          display: "grid",
                          gridTemplateColumns: "88px minmax(0, 1fr)",
                          columnGap: 1.5,
                          px: 1.5,
                          py: 0.75,
                          "& + &": {
                            borderTop: "1px solid",
                            borderColor: "divider",
                          },
                        }}
                      >
                        <Typography
                          sx={{ typography: "s3", color: "text.tertiary" }}
                        >
                          {`${label} · ${names.length}`}
                        </Typography>
                        <Stack spacing={0.25} sx={{ minWidth: 0 }}>
                          {names.slice(0, USE_SHOWN).map((name, k) => (
                            <Typography
                              key={`${name}-${k}`}
                              noWrap
                              title={name}
                              sx={{ typography: "s3", color: "text.secondary" }}
                            >
                              {name}
                            </Typography>
                          ))}
                          {names.length > USE_SHOWN && (
                            <Typography
                              sx={{ typography: "s3", color: "text.tertiary" }}
                            >
                              {`and ${names.length - USE_SHOWN} more`}
                            </Typography>
                          )}
                        </Stack>
                      </Box>
                    ))}
                  </Box>
                )}
                {(refresh || s.revoked || choosing) && (
                  <Stack
                    spacing={0.5}
                    sx={{ px: 2, pb: 1 }}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    {refresh && !s.revoked && (
                      <Stack direction="row" alignItems="center" spacing={1}>
                        <Typography
                          sx={{ typography: "s3", color: "text.secondary" }}
                        >
                          {`Changed in the Knowledge base since you added it${s.kb.keptRev === refresh.latest ? " · you kept your copy" : ""}`}
                        </Typography>
                        {!readOnly && (
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={() => setRefreshing(s.id)}
                          >
                            Review refresh
                          </Button>
                        )}
                      </Stack>
                    )}
                    {s.revoked && (
                      <Stack direction="row" alignItems="center" spacing={1}>
                        <Typography
                          sx={{ typography: "s3", color: "status.warning.fg" }}
                        >
                          {`Removed from the Knowledge base · keeping your copy`}
                        </Typography>
                      </Stack>
                    )}
                    {choosing && (
                      <Stack direction="row" alignItems="center" spacing={1}>
                        <Typography
                          sx={{ typography: "s3", color: "status.warning.fg" }}
                        >
                          Personal data · choose how to handle it
                        </Typography>
                        {!readOnly && (
                          <Button
                            size="small"
                            variant="outlined"
                            onClick={() => setOpen(s.id, "privacy")}
                          >
                            Choose
                          </Button>
                        )}
                      </Stack>
                    )}
                  </Stack>
                )}
              </Box>
            );
          })}
          {inEnv.length > 0 && (
            <Box
              sx={{
                px: 2,
                py: 1.25,
                borderTop: "1px solid",
                borderColor: "divider",
              }}
            >
              {/* One line (#96): who gets which files, the detail and how
                  it's decided on hover — the column above already says it
                  per file. */}
              <Tooltip
                arrow
                placement="top-start"
                title={[
                  ...[
                    ["agent", "Agent"],
                    ["caller", "Caller"],
                    ["evaluator", "Grader"],
                  ].map(
                    ([k, label]) =>
                      `${label}: ${views[k].length ? views[k].map((v) => `${v.source.name} (${v.how.toLowerCase()})`).join(", ") : "none"}`,
                  ),
                  "From each file's Who reads it — set by how it's used, changeable on the file.",
                ].join("\n")}
                componentsProps={{
                  tooltip: { sx: { whiteSpace: "pre-line" } },
                }}
              >
                <Typography
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    width: "fit-content",
                  }}
                >
                  {`In a run · agent ${views.agent.length} · caller ${views.caller.length} · grader ${views.evaluator.length} ${views.evaluator.length === 1 ? "file" : "files"}`}
                </Typography>
              </Tooltip>
            </Box>
          )}
        </Box>
      )}

      {archived.length > 0 && (
        <Box sx={{ borderTop: "1px solid", borderColor: "divider" }}>
          <Typography
            sx={{ typography: "s3", color: "text.tertiary", px: 2, pt: 1.25 }}
          >
            {`Removed · ${archived.length}`}
          </Typography>
          {archived.map((s) => (
            <Stack
              key={s.id}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2, py: 0.75 }}
            >
              <Typography
                noWrap
                sx={{ typography: "s2", flex: 1, minWidth: 0 }}
              >
                {s.name}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                New versions don&apos;t use it. Past runs still show it.
              </Typography>
              {!readOnly && (
                <Button
                  size="small"
                  variant="outlined"
                  onClick={() => {
                    patch(
                      sourceEditPatch(
                        envState,
                        s.id,
                        { archived: false },
                        `${s.name} restored`,
                      ),
                    );
                    enqueueSnackbar(`Restored ${s.name}.`, {
                      variant: "success",
                    });
                  }}
                >
                  Restore
                </Button>
              )}
            </Stack>
          ))}
        </Box>
      )}

      <SourceDrawer
        open={!!open && sources.some((s) => s.id === open)}
        onClose={() => setOpen(null)}
        env={env}
        envState={envState}
        patch={patch}
        sourceId={open}
        focusAt={openAt}
        focusTab={openTab}
        readOnly={readOnly}
        onReviewRefresh={(src) => setRefreshing(src.id)}
      />
      <SideDrawer open={!!review} onClose={() => setReview(null)} width={560}>
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
              <Typography sx={{ typography: "m2", fontWeight: 600 }}>
                Review proposed changes
              </Typography>
              <Typography
                noWrap
                sx={{ typography: "s3", color: "text.tertiary" }}
              >
                {/* Where it came from (#95): it opens once the uploaded
                    files are read. */}
                {`What the builder read in ${(review || [])
                  .map(
                    (id) =>
                      (envState.sources || []).find((s) => s.id === id)?.name,
                  )
                  .filter(Boolean)
                  .join(", ")} — nothing changes until you add it`}
              </Typography>
            </Box>
            <IconButton
              size="small"
              onClick={() => setReview(null)}
              aria-label="Close"
            >
              <Iconify
                icon="solar:close-circle-linear"
                width={20}
                sx={{ color: "text.tertiary" }}
              />
            </IconButton>
          </Stack>
          <Box sx={{ flex: 1, minHeight: 0 }}>
            {/* Each file loads, then its proposals fill in. */}
            {review &&
              review
                .map((id) => (envState.sources || []).find((x) => x.id === id))
                .filter((x) => x && isReading(x))
                .map((x) => (
                  <Stack
                    key={x.id}
                    direction="row"
                    alignItems="center"
                    spacing={1}
                    sx={{
                      px: 2.5,
                      py: 1.25,
                      borderBottom: "1px solid",
                      borderColor: "divider",
                    }}
                  >
                    <CircularProgress
                      size={14}
                      thickness={5}
                      sx={{ color: "text.secondary", flexShrink: 0 }}
                    />
                    <Typography
                      sx={{ typography: "s1", color: "text.secondary" }}
                    >
                      {`Reading ${x.name}…`}
                    </Typography>
                  </Stack>
                ))}
            {review && (
              <UploadRoles
                disabled={readOnly}
                sources={review
                  .map((id) =>
                    (envState.sources || []).find((x) => x.id === id),
                  )
                  .filter((x) => x && !isReading(x) && !x.kb)}
                onRole={(src, role) =>
                  patch(
                    sourceEditPatch(
                      envState,
                      src.id,
                      { role, visibility: defaultVisibility(role) },
                      `${src.name} is now ${roleLabel(role)}`,
                    ),
                  )
                }
              />
            )}
            {review &&
              review.some((id) =>
                (envState.sources || []).some(
                  (x) => x.id === id && !isReading(x),
                ),
              ) && (
                <DataProposalCard
                  key={review
                    .filter((id) =>
                      (envState.sources || []).some(
                        (x) => x.id === id && !isReading(x),
                      ),
                    )
                    .join(",")}
                  layout="drawer"
                  env={env}
                  sourceIds={review.filter((id) =>
                    (envState.sources || []).some(
                      (x) => x.id === id && !isReading(x),
                    ),
                  )}
                  onSaved={() => setReview(null)}
                  onOpenSource={(id) => {
                    setReview(null);
                    if (id) setOpen(id);
                  }}
                />
              )}
          </Box>
        </Stack>
      </SideDrawer>
      {refreshing && (
        <ReviewRefreshDialog
          open
          onClose={() => setRefreshing(null)}
          env={env}
          envState={envState}
          patch={patch}
          sourceId={refreshing}
        />
      )}
    </SectionCard>
  );
}

SourcesSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
};
