import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Stack, Tab, Typography } from "@mui/material";
import { CustomTabs } from "src/components/tabs/tabs";
import { ConfirmDialog } from "src/components/custom-dialog";
import {
  sourceSummaryKey,
  useEnvironmentSources,
  useSourceCapabilities,
} from "src/api/simulate-environments/sources";
import SideDrawer from "../../components/SideDrawer";
import SourceUsage from "./SourceUsage";
import { PersonalDataTab } from "./PersonalDataChoice";
import WhatWasRead from "./WhatWasRead";
import { SOURCES_COPY, isSaved } from "./sources.constants";

const PURPOSES = ["policy", "reference", "seed_records"];

// Who reads a file by default once its role is picked.
const defaultVisibility = (role) => {
  if (role === "evaluation_reference") return "evaluator";
  return role === "reference" ? "agent" : "tool";
};

const fromKb = (source) => source.origin?.kind === "knowledge_base";

// What the file feeds once saved, from what reading it found.
const usageOf = (source) => {
  const parsed = source.ui?.parsed;
  const saved = isSaved(source);
  return {
    rules:
      saved && source.role === "policy" && parsed?.kind === "text"
        ? parsed.passages.filter((passage) => passage.rule)
        : [],
    seeds:
      saved && source.role === "seed_records" && parsed?.kind === "table"
        ? [parsed]
        : [],
  };
};

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

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

function UsedBy({ source, readOnly, onRemove }) {
  const [confirming, setConfirming] = useState(false);
  const saved = isSaved(source);
  const { rules, seeds } = usageOf(source);
  const users = [
    rules.length && plural(rules.length, "rule"),
    seeds.length && plural(seeds.length, "seed table"),
  ].filter(Boolean);

  return (
    <Box>
      <KV label="Rules">
        {rules.length
          ? rules.map((passage) => <Box key={passage.id}>{passage.rule}</Box>)
          : "None"}
      </KV>
      <KV label="Scenarios">None yet</KV>
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
      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={
          saved
            ? `Remove ${source.name} from this environment?`
            : SOURCES_COPY.removeTitle(source.name)
        }
        content={
          saved
            ? `New versions won't use it. ${users.length ? `It's used by ${users.join(", ")}. They keep citing it on the versions they were made on. ` : ""}Past runs still show it. You can restore it from Removed.`
            : "It isn't in the world yet. Its reading and mapping are discarded; add it again from Sources."
        }
        action={
          <Button
            size="small"
            variant="contained"
            color="error"
            onClick={() => {
              setConfirming(false);
              onRemove();
            }}
          >
            {SOURCES_COPY.removeConfirm}
          </Button>
        }
      />
    </Box>
  );
}

UsedBy.propTypes = {
  source: PropTypes.object.isRequired,
  readOnly: PropTypes.bool,
  onRemove: PropTypes.func.isRequired,
};

function Versions({ source }) {
  const kb = fromKb(source);
  const saved = isSaved(source);
  const firstNote = kb
    ? `Copied from the Knowledge base · ${source.origin.collection}`
    : "Uploaded";
  const versions = Array.from({ length: source.version }, (_, i) => i + 1)
    .reverse()
    .map((v) => {
      const current = v === source.version;
      let label = kb ? "earlier copy" : `v${v}`;
      if (kb && current) label = SOURCES_COPY.yourCopy;
      return {
        v,
        label: `${label}${current && saved ? " · in use" : ""}`,
        note: v === 1 ? firstNote : "Replaced with a new version",
      };
    });

  return (
    <Stack spacing={1.5}>
      {kb && (
        <Box>
          <KV label="Knowledge base">{`${source.origin.collection} · ${SOURCES_COPY.yourCopy}`}</KV>
          <KV label="Snapshot">
            A copy of that revision. It isn&apos;t linked live, so edits in the
            Knowledge base don&apos;t change this environment.
          </KV>
          <KV label="Refresh">
            {source.ui?.changed_upstream
              ? SOURCES_COPY.changedUpstream
              : "Up to date. A new revision waits for you to review it; it is never pulled in on its own."}
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
    </Stack>
  );
}

Versions.propTypes = { source: PropTypes.object.isRequired };

function MappingSlot() {
  return (
    <Typography sx={{ typography: "s1", color: "text.secondary" }}>
      This tab shows the file&apos;s mapping.
    </Typography>
  );
}

// Each tab's body, by the `sourceTab` id. Every body gets
// `{ envId, source, at, readOnly, onEdit, onRemove }`.
const TAB_BODIES = {
  preview: WhatWasRead,
  mapping: MappingSlot,
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
 * uses it and its versions. `tab` and `at` are read once, so the parent keys
 * the drawer on them; `at` opens What was read at the cited passage or row.
 * Edits go to the sources summary cache.
 */
export default function SourceDrawer({
  envId,
  sourceId,
  tab = null,
  at = null,
  readOnly = false,
  onClose,
}) {
  const queryClient = useQueryClient();
  const { data: summary } = useEnvironmentSources(envId);
  const { data: capabilities } = useSourceCapabilities(envId);
  const [usageOpen, setUsageOpen] = useState(false);
  const [picked, setPicked] = useState(at ? "preview" : tab || "preview");
  const fileRef = useRef(null);

  const source = summary?.sources.find((one) => one.source_id === sourceId);
  if (!source || source.ui?.removed) return null;

  const update = (change) =>
    queryClient.setQueryData(sourceSummaryKey(envId), (current) =>
      current ? { ...current, sources: change(current.sources) } : current,
    );
  const onEdit = (change) =>
    update((sources) =>
      sources.map((one) => (one.source_id === sourceId ? change(one) : one)),
    );
  const saved = isSaved(source);
  // A saved file goes to Removed; one not saved yet is just dropped.
  const onRemove = () => {
    if (saved) onEdit((one) => ({ ...one, ui: { ...one.ui, removed: true } }));
    else
      update((sources) => sources.filter((one) => one.source_id !== sourceId));
    onClose();
  };

  const tabs = tabsOf(source);
  const current = tabs.some(([id]) => id === picked) ? picked : "preview";
  const Body = TAB_BODIES[current];
  const snapshot = summary.snapshot_digest
    ?.replace(/^sha256:/, "")
    .slice(0, 12);
  const replaceable = !readOnly && saved && source.ui?.parsed?.kind === "text";

  return (
    <SideDrawer open onClose={onClose} width={560}>
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

        <Box
          sx={{ px: 2.5, borderBottom: "1px solid", borderColor: "divider" }}
        >
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
          <Body
            envId={envId}
            source={source}
            at={at}
            readOnly={readOnly}
            onEdit={onEdit}
            onRemove={onRemove}
          />
        </Box>

        {replaceable && (
          <Stack
            direction="row"
            justifyContent="flex-end"
            sx={{
              px: 2.5,
              py: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
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
              accept={capabilities?.upload.formats
                .map((ext) => `.${ext}`)
                .join(",")}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (!file) return;
                setPicked("versions");
                // shortcut: the sources stub can't re-read a file as a new version; wire it when the API can.
                enqueueSnackbar(
                  `Reading ${file.name} as a new version of ${source.name} isn't available yet.`,
                  { variant: "info" },
                );
              }}
            />
          </Stack>
        )}
      </Stack>
    </SideDrawer>
  );
}

SourceDrawer.propTypes = {
  envId: PropTypes.string,
  sourceId: PropTypes.string.isRequired,
  // The tab to open on: preview, mapping, privacy, usage or versions.
  tab: PropTypes.string,
  // The cited passage id or sample-row value to show in What was read.
  at: PropTypes.string,
  readOnly: PropTypes.bool,
  onClose: PropTypes.func.isRequired,
};
