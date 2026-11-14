import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  List,
  ListItemButton,
  Stack,
  Typography,
} from "@mui/material";

import { useSkill, useSkillCompare, useSkillLabels } from "src/api/skills";
import { useParams, useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import ContentSkeleton from "src/components/content-skeleton";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import ObjectHeader from "../components/ObjectHeader";
import TechnicalDetails from "../components/TechnicalDetails";
import Section from "src/components/section";
import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";
import TextDiff from "src/sections/skills/components/TextDiff";
import VersionSelect from "src/sections/skills/components/VersionSelect";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import IdentityChip from "../components/IdentityChip";
import RestoreVersionDialog from "../versions/RestoreVersionDialog";

function parseRef(value, fallback) {
  if (value == null) return fallback;
  if (value === "draft" || value.startsWith("label:")) return value;
  if (/^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value)))
    return Number(value);
  return null;
}

function resolveRef(value, skill, labels) {
  if (typeof value !== "string" || !value.startsWith("label:")) return value;
  const name = value.slice(6);
  return name === "latest"
    ? skill?.latestVersion
    : labels?.find((label) => label.name === name)?.version;
}

const refTitle = (value) => (value === "draft" ? "Your draft" : `v${value}`);

export default function SkillCompareView() {
  const { skillId } = useParams();
  const router = useRouter();
  const [params, setParams] = useSearchParams();
  const preview = usePreviewState();
  const requestedReturn = params.get('returnTo');
  const returnTo = requestedReturn?.startsWith(`${paths.dashboard.skills.reviews}?`) ? requestedReturn : `${paths.dashboard.skills.detail(skillId)}?tab=versions`;
  const returnLabel = returnTo === requestedReturn ? 'Return to review' : 'Back to versions';
  const [selectedPath, setSelectedPath] = useState(null);
  const [restore, setRestore] = useState(null);
  const skill = useSkill(skillId);
  const labels = useSkillLabels(skillId);
  const latest = skill.data?.latestVersion;
  const from = parseRef(
    params.get("from"),
    latest ? Math.max(1, latest - 1) : null,
  );
  const to = parseRef(params.get("to"), latest || null);
  const resolvedFrom = resolveRef(from, skill.data, labels.data);
  const resolvedTo = resolveRef(to, skill.data, labels.data);
  const needsLabels = [from, to].some(
    (ref) =>
      typeof ref === "string" &&
      ref.startsWith("label:") &&
      ref !== "label:latest",
  );
  const compare = useSkillCompare(
    resolvedFrom != null && resolvedTo != null ? skillId : undefined,
    resolvedFrom,
    resolvedTo,
  );
  const files = [...(compare.data?.files || [])].sort(
    (a, b) =>
      Number(a.status === "unchanged") - Number(b.status === "unchanged") ||
      a.path.localeCompare(b.path),
  );
  const selected = files.find((file) => file.path === selectedPath) || files[0];
  const loading =
    skill.isLoading || (needsLabels && labels.isLoading) || compare.isLoading;
  const queryError =
    skill.isError || (needsLabels && labels.isError) || compare.isError;
  const noVersions =
    skill.data && !latest && from !== "draft" && to !== "draft";
  const invalidRefs =
    !loading && !noVersions && (resolvedFrom == null || resolvedTo == null);
  const setRef = (key, value) => {
    setSelectedPath(null);
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set(key, String(value));
      return next;
    });
  };
  const retry = () => {
    if (preview)
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("state");
          return next;
        },
        { replace: true },
      );
    skill.refetch();
    if (needsLabels) labels.refetch();
    if (resolvedFrom != null && resolvedTo != null) compare.refetch();
  };
  const showLoading = preview === "loading" || (!preview && loading);
  const showError =
    preview === "error" || (!preview && (queryError || invalidRefs));
  const showEmpty = preview === "empty" || noVersions || files.length === 0;

  return (
    <Stack spacing={2} sx={{ p: { xs: 2, md: 3 }, minWidth: 0, flex: 1 }}>
      <ObjectHeader
        crumbs={[{ label: "Skills", to: paths.dashboard.skills.root }, { label: skill.data?.name || "Skill", to: paths.dashboard.skills.detail(skillId) }, { label: "Compare" }]}
        title="Compare versions"
        purpose="Review file changes before choosing a version."
        actions={<><Button variant="text" onClick={() => router.push(returnTo)}>{returnLabel}</Button>{!showLoading && !showError && !showEmpty && skill.data && [...new Set([resolvedFrom, resolvedTo])].filter((ref) => typeof ref === "number" && ref !== latest).map((number) => <Button key={number} variant="outlined" onClick={() => setRestore(number)}>Restore v{number} as new version</Button>)}</>}
      />
      {skill.data && (
        <Stack
          direction={{ xs: "column", sm: "row" }}
          spacing={2}
          alignItems={{ sm: "flex-end" }}
        >
          <Stack
            spacing={0.5}
            role="group"
            aria-label="Compare from"
            sx={{ flex: 1, minWidth: 0 }}
          >
            <VersionSelect label="From"
              skillId={skillId}
              value={from ?? ""}
              onChange={(value) => setRef("from", value)}
              includeDraft
            />
          </Stack>
          <Stack
            spacing={0.5}
            role="group"
            aria-label="Compare to"
            sx={{ flex: 1, minWidth: 0 }}
          >
            <VersionSelect label="To"
              skillId={skillId}
              value={to ?? ""}
              onChange={(value) => setRef("to", value)}
              includeDraft
            />
          </Stack>
        </Stack>
      )}
      {showLoading ? (
        <ContentSkeleton variant="panel" rows={6} label="Comparing versions" />
      ) : showError ? (
        <ErrorState
          title={
            invalidRefs
              ? "Choose two available versions or your draft"
              : "Could not compare these versions"
          }
          detail={
            invalidRefs
              ? "A selected version or label is unavailable. Choose another selector above; missing labels never fall back to latest."
              : undefined
          }
          onRetry={retry}
        />
      ) : showEmpty ? (
        <EmptyState
          title={
            noVersions
              ? "No published versions to compare"
              : "No files to compare"
          }
          description="Publish a version, or choose your draft and a published version to review file changes."
        />
      ) : (
        <Stack spacing={2}>
          <Typography variant="s1">{files.every((file) => file.status === 'unchanged') ? `No files changed between ${refTitle(resolvedFrom)} and ${refTitle(resolvedTo)}.` : `${files.filter((file) => file.status !== 'unchanged').length} ${files.filter((file) => file.status !== 'unchanged').length === 1 ? 'file changed' : 'files changed'} between ${refTitle(resolvedFrom)} and ${refTitle(resolvedTo)}: ${files.filter((file) => file.status !== 'unchanged').map((file) => file.path).join(', ')}`}</Typography>
          {selected && <TechnicalDetails><Stack direction="row" useFlexGap flexWrap="wrap" spacing={1.5}>
            {selected.fromSha256 && <IdentityChip value={selected.fromSha256} label={`${refTitle(resolvedFrom)} file digest`} />}
            {selected.toSha256 && <IdentityChip value={selected.toSha256} label={`${refTitle(resolvedTo)} file digest`} />}
          </Stack></TechnicalDetails>}
          <Stack
            direction={{ xs: "column", md: "row" }}
            spacing={2}
            alignItems="stretch"
          >
            <Box
              sx={{
                width: { xs: "100%", md: 224 },
                flexShrink: 0,
                minWidth: 0,
              }}
            >
              <Section title={`Files (${files.length})`}>
                <List disablePadding aria-label="Compared files">
                  {files.map((file) => (
                    <ListItemButton
                      key={file.path}
                      selected={selected?.path === file.path}
                      onClick={() => setSelectedPath(file.path)}
                      sx={{ px: 1, py: 1 }}
                    >
                      <Stack spacing={0.5} sx={{ minWidth: 0 }}>
                        <Typography
                          variant="s1"
                          noWrap
                          title={file.path}
                          sx={{ fontFamily: "monospace" }}
                        >
                          {file.path}
                        </Typography>
                        <FileStatusBadge status={file.status} />
                      </Stack>
                    </ListItemButton>
                  ))}
                </List>
              </Section>
            </Box>
            {selected && (
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Section
                  title={selected.path}
                >
                  {selected.status === "unchanged" ? (
                    <Typography
                      variant="s1"
                      color="text.secondary"
                      sx={{ py: 2 }}
                    >
                      This file is identical in both versions.
                    </Typography>
                  ) : (
                    <Box sx={{ overflowX: "auto", minWidth: 0 }}>
                      <TextDiff
                        fromText={selected.fromText ?? ""}
                        toText={selected.toText ?? ""}
                        fromTitle={refTitle(resolvedFrom)}
                        toTitle={refTitle(resolvedTo)}
                      />
                    </Box>
                  )}
                </Section>
              </Box>
            )}
          </Stack>

        </Stack>
      )}
      {restore !== null && skill.data && (
        <RestoreVersionDialog
          skill={skill.data}
          number={restore}
          onClose={() => setRestore(null)}
        />
      )}
    </Stack>
  );
}
