import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Box,
  Button,
  IconButton,
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
import Iconify from "src/components/iconify";
import PageHeader from "src/components/page-header";
import Section from "src/components/section";
import FileStatusBadge from "src/sections/skills/components/FileStatusBadge";
import TextDiff from "src/sections/skills/components/TextDiff";
import VersionSelect from "src/sections/skills/components/VersionSelect";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import RestoreVersionDialog from "../versions/RestoreVersionDialog";
import CompareSummary from "./CompareSummary";

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
    <Stack spacing={2} sx={{ p: 3, minWidth: 0, flex: 1 }}>
      <PageHeader
        title="Compare versions"
        description={
          skill.data?.name || "Review changes across every file in a skill."
        }
        back={
          <IconButton
            aria-label="Back to skill versions"
            onClick={() =>
              router.push(
                `${paths.dashboard.skills.detail(skillId)}?tab=versions`,
              )
            }
          >
            <Iconify icon="solar:alt-arrow-left-linear" />
          </IconButton>
        }
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
            <Typography variant="s3" color="text.secondary">
              From
            </Typography>
            <VersionSelect
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
            <Typography variant="s3" color="text.secondary">
              To
            </Typography>
            <VersionSelect
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
          <CompareSummary summary={compare.data.summary} />
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
                  actions={<FileStatusBadge status={selected.status} />}
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
          {skill.data && (
            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
              {[...new Set([resolvedFrom, resolvedTo])]
                .filter((ref) => typeof ref === "number")
                .map((number) => (
                  <Button
                    key={number}
                    variant="outlined"
                    onClick={() => setRestore(number)}
                  >
                    Restore v{number} as new version
                  </Button>
                ))}
            </Stack>
          )}
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
