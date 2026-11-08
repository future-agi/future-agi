import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, MenuItem, Stack, Tab, TextField, Typography } from "@mui/material";
import PageHeader from "src/components/page-header";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import { SegmentedTabs } from "src/components/tabs/tabs";
import FileTree from "../components/FileTree";
import ContextIdentityStrip from "./ContextIdentityStrip";
import ContextLinksPanel from "./ContextLinksPanel";

// Intent: inspect the exact viewed release and choose an explicit editing origin.
// Hierarchy: selected/latest/production strip, AGENTS root, read-only provenance.
// Palette/depth/surfaces: neutral source paper and divider, no delivery inference.
// Type/spacing: m3/s1/s3, 12px groups; file rail stacks below narrow-width identity.
function Detail({ scopeKey, query, versionQuery, fileQuery, selectedPath = "AGENTS.md", onSelectPath, onSelectVersion, versions = [], onEditViewed, onResumeDraft, onPreserveDraft, sections = {}, state }) {
  const [tab, setTab] = useState("context");
  const scoped = [query, versionQuery, fileQuery].every((item) => item.scopeKey === scopeKey);
  if (!scoped || query.isLoading || state === "loading") return <ContentSkeleton />;
  if (query.error || state === "error") return <ErrorState title="Context unavailable" detail="The context could not be read in your current scope." onRetry={query.refetch} />;
  const context = query.data?.context;
  if (!context) return <ErrorState title="Context not found" detail="Choose an authorized context in this workspace." />;
  const viewed = versionQuery.data?.version;
  const descriptor = versionQuery.data?.descriptor;
  const manifest = versionQuery.data?.files || [];
  const selected = manifest.find((file) => file.path === selectedPath);
  const content = fileQuery.data?.path === selectedPath && fileQuery.data?.versionId === viewed?.id ? fileQuery.data.text : null;
  const tabs = [["context", "Context"], ["links", "Linked skills"], ["tests", "Tests"], ["versions", "Versions"], ["consumers", "Consumers"], ["use", "Use"]];
  const openFile = (path) => { if (manifest.some((file) => file.path === path)) onSelectPath?.(path); };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title={context.name} description={descriptor?.description || context.description} />
    <ContextIdentityStrip viewed={viewed} latestVersion={context.latestVersion} productionVersion={context.labels?.find((label) => label.name === "production")?.version} draft={query.data.ownDraft} />
    <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
      {query.data.ownDraft && <Button variant="contained" disabled={!onResumeDraft} onClick={onResumeDraft}>Resume your draft</Button>}
      <Button variant={query.data.ownDraft ? "outlined" : "contained"} disabled={!viewed || !onEditViewed || (Boolean(query.data.ownDraft) && !onPreserveDraft) || versionQuery.isLoading || Boolean(versionQuery.error)} onClick={() => query.data.ownDraft ? onPreserveDraft?.(viewed) : onEditViewed(viewed)}>Edit viewed {viewed ? `v${viewed.number}` : "version"}</Button>
      <TextField select size="small" label="Viewed release" value={viewed?.number || ""} disabled={!onSelectVersion} onChange={(event) => onSelectVersion(Number(event.target.value))}>
        {!viewed && <MenuItem value="">Not published</MenuItem>}{versions.map((version) => <MenuItem key={version.id} value={version.number}>v{version.number}</MenuItem>)}
      </TextField>
    </Stack>
    {query.data.ownDraft && <Typography variant="s3">Editing a viewed release first preserves and suspends your existing draft. It never overwrites it.</Typography>}
    <SegmentedTabs value={tab} onChange={(_, value) => setTab(value)} aria-label="Context detail sections" sx={{ maxWidth: "100%" }}>
      {tabs.map(([value, label]) => <Tab key={value} value={value} label={label} disabled={!["context", "links"].includes(value) && !sections[value]} />)}
    </SegmentedTabs>
    {versionQuery.isLoading ? <ContentSkeleton /> : versionQuery.error ? <ErrorState title="Release unavailable" onRetry={versionQuery.refetch} /> : <>
      {tab === "context" && <Stack direction={{ xs: "column", md: "row" }} spacing={2} sx={{ minWidth: 0 }}>
        <Box sx={{ width: { xs: "100%", md: 240 }, flexShrink: 0, minWidth: 0 }}><FileTree files={manifest} pinned={["AGENTS.md", ".futureagi/context.json"]} selectedPath={selectedPath} onSelect={openFile} readOnly /></Box>
        <Stack spacing={1.5} sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="m3" sx={{ overflowWrap: "anywhere" }}>{selectedPath}</Typography>
          {selected?.origin?.kind === "linked" && <Alert severity="info">Read-only linked content · source version {selected.origin.versionId}. The included bytes belong to this parent release.</Alert>}
          {selected?.origin?.kind === "generated" && <Alert severity="info">Generated descriptor · read-only. Selector policy and accepted dependency identities participate in the release digest.</Alert>}
          {fileQuery.isLoading ? <ContentSkeleton /> : fileQuery.error ? <ErrorState title="File unavailable" onRetry={fileQuery.refetch} /> : content == null ? <Typography variant="s3">Select a retained file. No content from another release is shown.</Typography> : <Box component="pre" tabIndex={0} aria-label={`${selectedPath} read-only source`} sx={{ m: 0, p: 1.5, bgcolor: "background.paper", border: 1, borderColor: "divider", borderRadius: 1, typography: "s1", fontFamily: "monospace", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{content}</Box>}
        </Stack>
      </Stack>}
      {tab === "links" && <ContextLinksPanel scopeKey={`${scopeKey}:${viewed?.id}`} readOnly session={{ contextSpec: { links: (descriptor?.links || []).map((link) => ({ skillId: link.skillId, selector: link.requested })) }, resolvedLock: { links: descriptor?.links || [] } }} />}
      {sections[tab]}
    </>}
  </Stack>;
}
Detail.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, versionQuery: PropTypes.object.isRequired, fileQuery: PropTypes.object.isRequired, selectedPath: PropTypes.string, onSelectPath: PropTypes.func, onSelectVersion: PropTypes.func, versions: PropTypes.array, onEditViewed: PropTypes.func, onResumeDraft: PropTypes.func, onPreserveDraft: PropTypes.func, sections: PropTypes.object, state: PropTypes.string };
export default function ContextDetailView(props) { return <Detail key={props.scopeKey} {...props} />; }
ContextDetailView.propTypes = Detail.propTypes;
