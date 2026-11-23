import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Fade, Stack, Tab, Typography } from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";
import {
  useEnvironmentSources,
  useUpdateSource,
} from "src/api/simulate-environments/sources";
import SideDrawer from "../../components/SideDrawer";
import usePrefersReducedMotion from "../../components/usePrefersReducedMotion";
import MappingTab from "./MappingTab";
import RemoveSourceDialog from "./RemoveSourceDialog";
import SourceUsage from "./SourceUsage";
import { PersonalDataTab } from "./PersonalDataChoice";
import WhatWasRead from "./WhatWasRead";
import { SOURCES_COPY, isSaved, usageOf } from "./sources.constants";

const PURPOSES = ["policy", "reference", "seed_records"];

// Who reads a file by default once its role is picked.
const defaultVisibility = (role) => {
  if (role === "evaluation_reference") return "evaluator";
  return role === "reference" ? "agent" : "tool";
};

const fromKb = (source) => source.origin?.kind === "knowledge_base";

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
      <Box sx={{ flex: 1, minWidth: 0, typography: "s1" }}>{children}</Box>
    </Stack>
  );
}

KV.propTypes = {
  label: PropTypes.string.isRequired,
  children: PropTypes.node,
};

function UsedBy({ envId, source, readOnly, onRemoved }) {
  const [confirming, setConfirming] = useState(false);
  const saved = isSaved(source);
  const { rules, scenarios, seeds } = usageOf(source);

  return (
    <Box>
      <KV label="Rules">
        {rules.length
          ? rules.map((passage) => <Box key={passage.id}>{passage.rule}</Box>)
          : "None"}
      </KV>
      <KV label="Scenarios">
        {scenarios.length
          ? scenarios.map((title) => <Box key={title}>{title}</Box>)
          : "None yet"}
      </KV>
      <KV label="Seeds">
        {seeds.length
          ? seeds
              .map(
                (seed) =>
                  `${seed.table} · ${seed.accepted.toLocaleString()} rows`,
              )
              .join(" · ")
          : "Nothing"}
      </KV>
      {!readOnly && (
        <Button
          size="small"
          variant="outlined"
          onClick={() => setConfirming(true)}
          sx={{ mt: 1.5 }}
        >
          {saved ? "Remove from this environment" : "Remove this file"}
        </Button>
      )}
      <RemoveSourceDialog
        envId={envId}
        source={source}
        open={confirming}
        onClose={() => setConfirming(false)}
        onRemoved={onRemoved}
      />
    </Box>
  );
}

UsedBy.propTypes = {
  envId: PropTypes.string,
  source: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onRemoved: PropTypes.func.isRequired,
};

const FIRST_NOTE = {
  upload: "Added from Sources",
  chat: "Attached in builder chat",
};
// Documents can be replaced by a newer copy; tables change through Mapping.
const REPLACEABLE = ["pdf", "md", "txt"];

function Versions({ source, readOnly, onReviewRefresh }) {
  const fileRef = useRef(null);
  const kb = fromKb(source);
  const saved = isSaved(source);
  const changed = saved && source.ui?.changed_upstream;
  const replaceable =
    !readOnly && saved && !kb && REPLACEABLE.includes(source.ext);
  const firstNote = kb
    ? `Copied from the Knowledge base · ${source.origin.collection}`
    : FIRST_NOTE[source.origin?.kind] || FIRST_NOTE.upload;
  const laterNote = kb
    ? "Refreshed from the Knowledge base"
    : "Replaced with a new version";
  const versions = Array.from({ length: source.version }, (_, i) => i + 1)
    .reverse()
    .map((v) => {
      const current = v === source.version;
      let label = kb ? "earlier copy" : `v${v}`;
      if (kb && current) label = SOURCES_COPY.yourCopy;
      return {
        v,
        label: `${label}${current && saved ? " · in use" : ""}`,
        note: v === 1 ? firstNote : laterNote,
      };
    });

  return (
    <Stack spacing={1.5}>
      {kb && (
        <Box>
          <KV label="Knowledge base">{`${source.origin.collection} · ${SOURCES_COPY.yourCopy}`}</KV>
          <KV label="Snapshot">
            A copy of the file as it was when you added it. It isn&apos;t linked
            live, so edits in the Knowledge base don&apos;t change this
            environment.
          </KV>
          <KV label="Refresh">
            {changed
              ? SOURCES_COPY.changedUpstream
              : "Up to date. A change in the Knowledge base waits for you to review it; it is never pulled in on its own."}
            {changed && !readOnly && onReviewRefresh && (
              <Button
                size="small"
                variant="outlined"
                onClick={() => onReviewRefresh(source)}
                sx={{ display: "flex", mt: 0.75 }}
              >
                {SOURCES_COPY.reviewRefresh}
              </Button>
            )}
          </KV>
        </Box>
      )}
      <Box>
        {versions.map((version) => (
          <KV key={version.v} label={version.label}>
            {version.note}
          </KV>
        ))}
      </Box>
      {replaceable && (
        <Box>
          <Button
            size="small"
            variant="outlined"
            onClick={() => fileRef.current?.click()}
          >
            Replace with a new version
          </Button>
          <input
            ref={fileRef}
            hidden
            type="file"
            accept={`.${source.ext}`}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              // shortcut: the sources stub can't re-read a file as a new version; wire it when the API can.
              enqueueSnackbar(
                `Reading ${file.name} as a new version of ${source.name} isn't available yet.`,
                { variant: "info" },
              );
            }}
          />
        </Box>
      )}
    </Stack>
  );
}

Versions.propTypes = {
  source: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onReviewRefresh: PropTypes.func,
};

// Each tab's body, by the `sourceTab` id. Every body gets
// `{ envId, source, at, readOnly, onEdit, onRemoved, onReviewRefresh, onSupply }`.
const TAB_BODIES = {
  preview: WhatWasRead,
  mapping: MappingTab,
  privacy: PersonalDataTab,
  usage: UsedBy,
  versions: Versions,
};

const tabsOf = (source) => {
  const table = source.ui?.parsed?.kind === "table";
  const personal = Object.keys(source.ui?.privacy || {}).length > 0;
  return [
    ["preview", "What was read"],
    ...(table ? [["mapping", "Mapping"]] : []),
    // A table always: a column the builder missed can be marked there.
    ...(table || personal ? [["privacy", "Personal data"]] : []),
    ["usage", "Used by"],
    ["versions", "Versions"],
  ];
};

/**
 * One file of the environment: what reading it found, how it's used, what
 * uses it and its versions. Stays mounted so it slides open and shut; the
 * body unmounts once closed and is keyed on `tab` and `at`, which it reads
 * once. `at` opens What was read at the cited passage or row. Edits go to
 * the sources summary cache.
 */
export default function SourceDrawer({
  envId,
  open = false,
  sourceId = null,
  tab = null,
  at = null,
  readOnly = false,
  onClose,
  onReviewRefresh,
  onSupply,
}) {
  const { data: summary } = useEnvironmentSources(envId);
  const source = summary?.sources.find((one) => one.source_id === sourceId);
  return (
    // A removed file stays in the summary, so the drawer slides shut with it.
    <SideDrawer
      open={open && Boolean(source) && !source.ui?.removed}
      onClose={onClose}
      width={560}
    >
      {source && (
        <SourceDrawerBody
          key={`${sourceId}:${tab}:${at}`}
          envId={envId}
          summary={summary}
          source={source}
          tab={tab}
          at={at}
          readOnly={readOnly}
          onClose={onClose}
          onReviewRefresh={onReviewRefresh}
          onSupply={onSupply}
        />
      )}
    </SideDrawer>
  );
}

SourceDrawer.propTypes = {
  envId: PropTypes.string,
  open: PropTypes.bool,
  // The file to show; kept by the parent while the drawer closes.
  sourceId: PropTypes.string,
  // The tab to open on: preview, mapping, privacy, usage or versions.
  tab: PropTypes.string,
  // The cited passage id or sample-row value to show in What was read.
  at: PropTypes.string,
  readOnly: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  // Gets the source when its Knowledge base file changed (Versions tab).
  onReviewRefresh: PropTypes.func,
  // Supply the records (Mapping tab): close the file, open Add source.
  onSupply: PropTypes.func,
};

function SourceDrawerBody({
  envId,
  summary,
  source,
  tab,
  at,
  readOnly,
  onClose,
  onReviewRefresh,
  onSupply,
}) {
  const updateSource = useUpdateSource(envId);
  const reduce = usePrefersReducedMotion();
  const [usageOpen, setUsageOpen] = useState(false);
  const [picked, setPicked] = useState(at ? "preview" : tab || "preview");

  const onEdit = (change) => updateSource(source.source_id, change);
  const saved = isSaved(source);

  const tabs = tabsOf(source);
  const current = tabs.some(([id]) => id === picked) ? picked : "preview";
  const Body = TAB_BODIES[current];
  const snapshot = summary.snapshot_digest
    ?.replace(/^sha256:/, "")
    .slice(0, 12);

  return (
    <Stack sx={{ height: "100%" }}>
      <Box sx={{ px: 2.5, pt: 2, pb: 1.25, pr: 6 }}>
        <Typography noWrap sx={{ typography: "m1" }}>
          {source.name}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {[
            source.ext.toUpperCase(),
            fromKb(source)
              ? SOURCES_COPY.yourCopy
              : SOURCES_COPY.version(source.version),
            source.ui?.owner,
            saved && snapshot
              ? `in snapshot ${snapshot}`
              : "not in the environment yet",
            source.status_note,
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      </Box>

      <Box sx={{ px: 2.5, pb: 1.5 }}>
        <SourceUsage
          compact={!usageOpen}
          onToggle={() => setUsageOpen((open) => !open)}
          role={source.role}
          visibility={source.visibility}
          disabled={readOnly || source.status === "unsupported"}
          onRole={(role) =>
            onEdit((one) => ({
              ...one,
              role,
              purpose: PURPOSES.includes(role) ? role : null,
              visibility: defaultVisibility(role),
            }))
          }
          onVisibility={(visibility) =>
            onEdit((one) => ({ ...one, visibility }))
          }
        />
      </Box>

      <Box sx={{ px: 2.5, borderBottom: "1px solid", borderColor: "divider" }}>
        <CustomTabs
          value={current}
          onChange={(_, id) => setPicked(id)}
          variant="scrollable"
          scrollButtons={false}
          sx={{ boxShadow: "none" }}
        >
          {tabs.map(([id, label]) => (
            <Tab key={id} value={id} label={label} />
          ))}
        </CustomTabs>
      </Box>

      <Box sx={{ flex: 1, overflowY: "auto", px: 2.5, py: 1.5 }}>
        {/* Keyed on the tab, so each tab's content fades in. */}
        <Fade key={current} in timeout={reduce ? 0 : undefined}>
          <Box>
            <Body
              envId={envId}
              source={source}
              at={at}
              readOnly={readOnly}
              onEdit={onEdit}
              onRemoved={onClose}
              onReviewRefresh={onReviewRefresh}
              onSupply={onSupply}
            />
          </Box>
        </Fade>
      </Box>
    </Stack>
  );
}

SourceDrawerBody.propTypes = {
  envId: PropTypes.string,
  summary: PropTypes.object.isRequired,
  source: PropTypes.object.isRequired,
  tab: PropTypes.string,
  at: PropTypes.string,
  readOnly: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
  onReviewRefresh: PropTypes.func,
  onSupply: PropTypes.func,
};
