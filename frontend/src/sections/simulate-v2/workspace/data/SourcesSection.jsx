import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  Dialog,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { SectionCard } from "../../components/primitives";
import CopyCommandButton from "../../components/CopyCommandButton";
import {
  ORG_SOURCES,
  SUPPORTED_LINE,
  participantViewsOf,
  readFile,
  roleLabel,
  visibilityLabel,
  usageOf,
  dataCli,
} from "../../_mock/orgData";
import DataProposalCard from "./DataProposalCard";
import SourceDrawer from "./SourceDrawer";

const STATUS = {
  ready: "Ready",
  "needs-attention": "Needs attention",
  unreadable: "Can't read",
  unsupported: "Not supported",
  "no-access": "No access",
};
const ago = (iso) => {
  const h = Math.round((Date.now() - Date.parse(iso || "")) / 3600000);
  return h < 1
    ? "just now"
    : h < 24
      ? `${h} h ago`
      : `${Math.round(h / 24)} d ago`;
};

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
  /* The open source lives in the URL (?source=), so a citation in builder chat can open it. */
  const [params, setParams] = useSearchParams();
  const open = params.get("source");
  const setOpen = (id) =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p);
        if (id) n.set("source", id);
        else n.delete("source");
        return n;
      },
      { replace: true },
    );
  const fileRef = useRef(null);
  const sources = (envState.sources || []).filter((s) => !s.archived);
  const inEnv = sources.filter((s) => s.inEnv);
  const views = participantViewsOf(inEnv);
  const snapshot = inEnv.find((s) => s.snapshot)?.snapshot;
  const newest = inEnv
    .map((s) => s.versions[s.versions.length - 1]?.at)
    .sort()
    .pop();

  const addFiles = (files) => {
    const read = files.map(readFile);
    patch({ sources: [...(envState.sources || []), ...read] });
    const usable = read.filter((s) => s.status !== "unsupported");
    if (usable.length) setReview(usable.map((s) => s.id));
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
    const s = {
      ...readFile({ name: o.name, size: 64000 }),
      owner: o.owner,
      byReference: true,
    };
    patch({ sources: [...(envState.sources || []), s] });
    setReview([s.id]);
  };

  return (
    <SectionCard
      title={`Sources · ${sources.length}`}
      subtitle={
        inEnv.length
          ? `${envState.dataMode === "private-sample" ? "Real records, a private sample" : "Synthetic data"} · snapshot ${snapshot || "—"} · updated ${ago(newest)}${sources.length > inEnv.length ? ` · ${sources.length - inEnv.length} not saved yet` : ""}`
          : sources.length
            ? `${sources.length} not saved yet — accept the builder's proposal to add ${sources.length === 1 ? "it" : "them"} to the world.`
            : "The files and organization data this world is built from — none yet. Attach a policy or records in builder chat, or add one here."
      }
      action={
        <Stack direction="row" spacing={1} alignItems="center">
          <CopyCommandButton command={dataCli.import(env, sources[0])} />
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
      <Menu
        anchorEl={anchor}
        open={!!anchor}
        onClose={() => setAnchor(null)}
        slotProps={{ paper: { sx: { width: 320 } } }}
      >
        <MenuItem
          onClick={() => {
            setAnchor(null);
            fileRef.current?.click();
          }}
          sx={{ alignItems: "flex-start", gap: 1, whiteSpace: "normal" }}
        >
          <Iconify icon="solar:upload-linear" width={16} sx={{ mt: "2px" }} />
          <Box>
            <Typography sx={{ typography: "s2", fontWeight: 600 }}>
              Upload files
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {SUPPORTED_LINE}
            </Typography>
          </Box>
        </MenuItem>
        <Typography
          sx={{
            typography: "s3",
            color: "text.tertiary",
            px: 2,
            pt: 1,
            pb: 0.5,
          }}
        >
          From your organization
        </Typography>
        {ORG_SOURCES.map((o) => (
          <MenuItem key={o.id} onClick={() => addOrg(o)} sx={{ gap: 1 }}>
            <Iconify
              icon={
                o.access
                  ? "solar:document-text-linear"
                  : "solar:lock-keyhole-minimalistic-linear"
              }
              width={16}
              sx={{ color: "text.secondary" }}
            />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography noWrap sx={{ typography: "s2" }}>
                {o.access ? o.name : `${o.owner} source`}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {o.access
                  ? `${o.owner} · added by reference`
                  : "No access — Request access"}
              </Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>
      <input
        ref={fileRef}
        hidden
        multiple
        type="file"
        onChange={(e) => {
          addFiles(Array.from(e.target.files || []));
          e.target.value = "";
        }}
      />

      {sources.length > 0 && (
        <Box>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns:
                "minmax(200px, 2fr) 1.2fr 1.1fr 1fr 64px 1fr",
              gap: 1.5,
              px: 2,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {[
              "Source",
              "Role",
              "Who reads it",
              "State",
              "Version",
              "Used by",
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
            return (
              <Box
                key={s.id}
                role="button"
                tabIndex={0}
                onClick={() => setOpen(s.id)}
                onKeyDown={(e) => e.key === "Enter" && setOpen(s.id)}
                sx={{
                  display: "grid",
                  gridTemplateColumns:
                    "minmax(200px, 2fr) 1.2fr 1.1fr 1fr 64px 1fr",
                  gap: 1.5,
                  px: 2,
                  py: 0.875,
                  cursor: "pointer",
                  "&:hover": { bgcolor: "action.hover" },
                  "& + &": { borderTop: "1px solid", borderColor: "divider" },
                }}
              >
                <Typography noWrap sx={{ typography: "s2", fontWeight: 600 }}>
                  {s.name}
                </Typography>
                <Typography noWrap sx={{ typography: "s2" }}>
                  {s.role ? roleLabel(s.role) : "—"}
                </Typography>
                <Typography noWrap sx={{ typography: "s2" }}>
                  {s.visibility ? visibilityLabel(s.visibility) : "—"}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color:
                      s.status === "ready"
                        ? "text.primary"
                        : "status.warning.fg",
                  }}
                >
                  {s.inEnv
                    ? STATUS[s.status]
                    : s.status === "ready"
                      ? "Not saved yet"
                      : STATUS[s.status]}
                </Typography>
                <Typography
                  sx={{ typography: "s2", fontVariantNumeric: "tabular-nums" }}
                >
                  {`v${s.version}${s.pendingRefresh ? ` · v${s.pendingRefresh.v} kept` : ""}`}
                </Typography>
                <Typography
                  noWrap
                  sx={{
                    typography: "s2",
                    color: n ? "text.primary" : "text.tertiary",
                  }}
                >
                  {n ? `${n} item${n === 1 ? "" : "s"}` : "Nothing yet"}
                </Typography>
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
              <Typography
                sx={{ typography: "s3", color: "text.tertiary", mb: 0.5 }}
              >
                Who sees what in a run
              </Typography>
              {[
                ["agent", "Agent under test"],
                ["caller", "Simulated caller"],
                ["evaluator", "Evaluator"],
              ].map(([k, label]) => (
                <Stack key={k} direction="row" spacing={2} sx={{ py: 0.25 }}>
                  <Typography
                    sx={{ typography: "s3", width: 136, flexShrink: 0 }}
                  >
                    {label}
                  </Typography>
                  <Typography
                    sx={{ typography: "s3", color: "text.secondary" }}
                  >
                    {views[k].length
                      ? views[k]
                          .map(
                            (v) => `${v.source.name} (${v.how.toLowerCase()})`,
                          )
                          .join(" · ")
                      : "None of these files"}
                  </Typography>
                </Stack>
              ))}
            </Box>
          )}
        </Box>
      )}

      <SourceDrawer
        open={!!open && sources.some((s) => s.id === open)}
        onClose={() => setOpen(null)}
        env={env}
        envState={envState}
        patch={patch}
        sourceId={open}
        readOnly={readOnly}
      />
      <Dialog
        open={!!review}
        onClose={() => setReview(null)}
        fullWidth
        maxWidth={false}
        PaperProps={{ sx: { maxWidth: 720, p: 2 } }}
      >
        <Typography sx={{ typography: "m2", fontWeight: 600, mb: 1.5 }}>
          Review what the builder proposes
        </Typography>
        {review && (
          <DataProposalCard
            env={env}
            sourceIds={review}
            onOpenSource={(id) => {
              setReview(null);
              if (id) setOpen(id);
            }}
          />
        )}
      </Dialog>
    </SectionCard>
  );
}

SourcesSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object.isRequired,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
};
