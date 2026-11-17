import { useId, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import { Box, Button, Drawer, IconButton, Link, MenuItem, Stack, TextField, Typography } from "@mui/material";

import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import StatusBadge from "src/components/status-badge";
import Iconify from "src/components/iconify";
import { coveredDrawerSx, useDrawerCovered } from "src/hooks/use-drawer-stack";
import { fDateTime, relativeTime } from "src/utils/format-time";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import { isSkillTracesEnabled } from "src/sections/skills/runtime/skillTracesFlag";
import { HOW_IT_WENT } from "src/sections/skills/runtime/runtimeCopy";
import { runtimeTabUrl } from "src/sections/skills/utils/traces";
import useLabelHistoryPage from "./useLabelHistoryPage";

// "How it went" (SKILL-TRACES §7.3 "Links into the card", UX-01): with
// skillTraces on, a production move that names its event links to the move
// card. The label-history response carries no event id yet (LabelEvent
// serializer and mapLabelEvent), so the link appears only once it does.
function howItWentHref(skillId, event) {
  const eventId = event.eventId ?? event.id;
  if (event.label !== "production" || event.toVersion == null || !eventId) return null;
  return runtimeTabUrl({ id: skillId, eventId, window: "24h" });
}

function HistoryEvents({ skillId, label }) {
  const filterId = useId();
  const [labelFilter, setLabelFilter] = useState(label || "");
  const [page, setPage] = useState(1);
  const history = useLabelHistoryPage(skillId, labelFilter, page);
  const preview = usePreviewState();
  const events = history.data?.results ?? [];
  const loading = preview === "loading" || (!preview && history.isPending);
  const error = preview === "error" || (!preview && history.isError);
  const empty = preview === "empty" || !events.length;
  const runtimeLinks = isSkillTracesEnabled();

  return (
    <>
      <TextField id={filterId} select size="small" label="Label filter" value={labelFilter} SelectProps={{ displayEmpty: true, SelectDisplayProps: { "aria-labelledby": `${filterId}-label` } }} onChange={(event) => { setLabelFilter(event.target.value); setPage(1); }}>
        <MenuItem value="">All labels</MenuItem>
        {[...new Set(['production', 'staging', label, ...events.map((event) => event.label)])].filter(Boolean).map((name) => <MenuItem key={name} value={name}>{name}</MenuItem>)}
      </TextField>
      {history.isFetching && !loading && <StatusBadge status="checking" label="Updating…" />}
      <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto" }} aria-busy={loading}>
        {loading ? (
          <ContentSkeleton
            variant="rows"
            rows={5}
            label="Loading label history"
          />
        ) : error ? (
          <ErrorState
            title="Couldn't load label history."
            detail="Your label changes are still saved. Try loading the history again."
            sx={{ flex: '0 0 auto', height: 'auto', justifyContent: 'flex-start', py: 3 }}
            onRetry={history.refetch}
          />
        ) : empty ? (
          <EmptyState
            variant="no-results"
            title={labelFilter ? `No changes to ${labelFilter}.` : "No label changes yet"}
            sx={{ flex: '0 0 auto', height: 'auto', justifyContent: 'flex-start', py: 3 }}
            description={labelFilter ? "Try another label or show all label changes." : "A change you just made can take a moment to appear."}
            action={labelFilter ? <Button variant="outlined" onClick={() => { setLabelFilter(""); setPage(1); }}>Show all labels</Button> : <Button variant="outlined" onClick={history.refetch}>Refresh</Button>}
          />
        ) : (
          <Stack component="ol" spacing={0} sx={{ m: 0, p: 0, listStyle: "none" }}>
            {events.map((event, index) => (
              <Box
                component="li"
                key={`${event.label}-${event.createdAt}-${index}`}
                sx={{ py: 1.5, borderBottom: 1, borderColor: "divider" }}
              >
                <Stack spacing={0.5}>
                  <Typography variant="s1" fontWeight={500}>
                    {event.label}:{" "}
                    {event.fromVersion == null
                      ? "Not set"
                      : `v${event.fromVersion}`}{" "}
                    →{" "}
                    {event.toVersion == null
                      ? "Removed"
                      : `v${event.toVersion}`}
                  </Typography>
                  <Typography variant="s3" color="text.secondary">
                    {event.actor?.name || "Unknown actor"}
                    {event.actor?.kind === "api_key" ? " (API key)" : ""} ·{" "}
                    <Box component="span" title={fDateTime(event.createdAt)}>{relativeTime(event.createdAt)}</Box>
                  </Typography>
                  {event.note && (
                    <Typography
                      variant="s1"
                      sx={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}
                    >
                      {event.note}
                    </Typography>
                  )}
                  {runtimeLinks && howItWentHref(skillId, event) && (
                    <Link component={RouterLink} to={howItWentHref(skillId, event)} variant="s3" underline="hover" sx={{ alignSelf: "flex-start" }}>
                      {HOW_IT_WENT}
                    </Link>
                  )}
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </Box>
      {!preview && (page > 1 || history.data?.totalPages > 1) && (
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={1}
          sx={{ flexShrink: 0 }}
        >
          <Button
            size="small"
            color="inherit"
            disabled={page === 1 || history.isFetching}
            onClick={() => setPage((current) => current - 1)}
          >
            Newer changes
          </Button>
          <Typography variant="s3" color="text.secondary" role="status">
            Page {page}{history.data ? ` of ${history.data.totalPages}` : ""}
          </Typography>
          <Button
            size="small"
            color="inherit"
            disabled={
              history.isFetching || error || !history.data ||
              page >= history.data.totalPages
            }
            onClick={() => setPage((current) => current + 1)}
          >
            Older changes
          </Button>
        </Stack>
      )}
    </>
  );
}
HistoryEvents.propTypes = {
  skillId: PropTypes.string.isRequired,
  label: PropTypes.string,
};

export default function LabelHistoryDrawer({ open, onClose, skill, label }) {
  const covered = useDrawerCovered(open);
  const titleId = useId();
  const name = typeof label === "string" ? label : label?.name;

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      sx={coveredDrawerSx(covered)}
      PaperProps={{
        role: "dialog",
        "aria-modal": true,
        "aria-labelledby": titleId,
        sx: { width: { xs: "100%", sm: 480 }, maxWidth: "100%" },
      }}
    >
      <Stack spacing={2} sx={{ p: 3, minHeight: 0, height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
          spacing={1}
        >
          <Typography id={titleId} variant="m1" component="h2" fontWeight={600}>
            Label history
          </Typography>
          <IconButton aria-label="Close label history" onClick={onClose}>
            <Iconify icon="solar:close-linear" />
          </IconButton>
        </Stack>
        <Typography variant="s1" color="text.secondary">
          Promotions, rollbacks and removals for {skill.name}.
        </Typography>
        {open && (
          <HistoryEvents
            key={`${skill.id}:${name || ""}`}
            skillId={skill.id}
            label={name}
          />
        )}
      </Stack>
    </Drawer>
  );
}

LabelHistoryDrawer.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  skill: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string.isRequired,
  }).isRequired,
  label: PropTypes.oneOfType([
    PropTypes.string,
    PropTypes.shape({ name: PropTypes.string }),
  ]),
};
