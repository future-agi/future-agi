import PropTypes from "prop-types";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { Box, Button, Collapse, Stack, Tab, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";

import {
  useSkillFile,
  useSkillVersion,
  getSkillErrorCode,
} from "src/api/skills";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import EmptyState from "src/components/empty-state";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { FormCodeEditor } from "src/components/form-code-editor";
import { SegmentedTabs } from "src/components/tabs/tabs";
import FileTree from "../components/FileTree";
import SkillMarkdownPreview from "../components/SkillMarkdownPreview";
import ChecksList from "../components/ChecksList";
import VersionSelect from "../components/VersionSelect";
import { languageForPath } from "../utils/paths";
import { formatBytes, formatTokens } from "../utils/format";
import { resolveViewedVersion } from "./detail-state";

function FileContent({ file, metadata, onOpenPath }) {
  const [mode, setMode] = useState("preview");
  const { control } = useForm({ values: { content: file.text } });
  const markdown = /\.(md|markdown)$/i.test(file.path);
  const copyFile = async () => {
    try {
      await navigator.clipboard.writeText(file.text);
      enqueueSnackbar("File copied", { variant: "success" });
    } catch {
      enqueueSnackbar("Couldn't copy the file. Try copying from Raw view.", {
        variant: "error",
      });
    }
  };

  return (
    <Stack spacing={1.5} sx={{ minWidth: 0 }}>
      <Stack
        direction="row"
        alignItems="center"
        useFlexGap
        flexWrap="wrap"
        gap={1}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            variant="s1"
            component="div"
            sx={{ fontFamily: "monospace", overflowWrap: "anywhere" }}
          >
            {file.path}
          </Typography>
          <Typography variant="s3" color="text.secondary">
            {formatBytes(metadata.sizeBytes)} ·{" "}
            {formatTokens(metadata.tokenEstimate)}
          </Typography>
        </Box>
        {markdown && (
          <SegmentedTabs
            value={mode}
            onChange={(_event, value) => setMode(value)}
            aria-label="File view"
          >
            <Tab value="preview" label="Preview" />
            <Tab value="raw" label="Raw" />
          </SegmentedTabs>
        )}
        <Button
          size="small"
          variant="outlined"
          startIcon={<Iconify icon="solar:copy-linear" width={16} />}
          onClick={copyFile}
        >
          Copy file
        </Button>
      </Stack>
      {markdown && mode === "preview" ? (
        <SkillMarkdownPreview
          content={file.text}
          currentPath={file.path}
          onOpenPath={onOpenPath}
        />
      ) : (
        <FormCodeEditor
          control={control}
          fieldName="content"
          readOnly
          language={languageForPath(file.path)}
          height="480px"
          options={{
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            wordWrap: "on",
            ariaLabel: `${file.path}, read only`,
          }}
        />
      )}
    </Stack>
  );
}

FileContent.propTypes = {
  file: PropTypes.object.isRequired,
  metadata: PropTypes.object.isRequired,
  onOpenPath: PropTypes.func.isRequired,
};

export default function FilesTab({ skill, previewEmpty = false }) {
  const [params, setParams] = useSearchParams();
  const [checksOpen, setChecksOpen] = useState(false);
  const viewed = resolveViewedVersion(skill, params.get("version"));
  const selectedPath = params.get("path") || "SKILL.md";
  const versionQuery = useSkillVersion(skill.id, viewed.number);
  const version = versionQuery.data;
  const metadata = version?.files.find((file) => file.path === selectedPath);
  const fileQuery = useSkillFile(
    skill.id,
    viewed.number,
    metadata ? selectedPath : null,
  );
  const updateParams = (changes) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      Object.entries(changes).forEach(([key, value]) =>
        next.set(key, String(value)),
      );
      return next;
    });
  const openPath = (path) => updateParams({ path });

  if (!skill.latestVersion || previewEmpty)
    return (
      <EmptyState
        title="No published files yet"
        description="Edit the draft and publish a version to view its files here."
      />
    );

  return (
    <Stack spacing={3}>
      <Stack
        direction="row"
        gap={1.5}
        flexWrap="wrap"
        alignItems="center"
      >
        <Box
          sx={{
            width: "max-content",
            maxWidth: "100%",
            "& .MuiFormControl-root": { width: "auto", maxWidth: "100%" },
          }}
        >
          <VersionSelect
            skillId={skill.id}
            value={viewed.value}
            onChange={(value) =>
              updateParams({ version: value, path: "SKILL.md" })
            }
          />
        </Box>
        {viewed.number && (
          <Typography variant="s3" color="text.secondary">
            Viewing immutable version v{viewed.number}
          </Typography>
        )}
      </Stack>
      {viewed.error ? (
        <ErrorState title="Version not found" detail={viewed.error} />
      ) : versionQuery.isPending ? (
        <ContentSkeleton variant="panel" label="Loading version" />
      ) : versionQuery.isError ? (
        <ErrorState
          title={
            getSkillErrorCode(versionQuery.error) === "version_not_found"
              ? "Version not found"
              : "Couldn't load this version"
          }
          detail="Choose another version or try again."
          onRetry={versionQuery.refetch}
        />
      ) : !version ? (
        <EmptyState title="Version not found" />
      ) : (
        <>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: {
                xs: "minmax(0, 1fr)",
                md: "240px minmax(0, 1fr)",
              },
              gap: 2,
            }}
          >
            <Box
              sx={{
                minWidth: 0,
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                p: 1,
              }}
            >
              <FileTree
                files={version.files}
                selectedPath={selectedPath}
                onSelect={openPath}
                readOnly
              />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              {!metadata ? (
                <EmptyState
                  title="File not found"
                  description="This path is not included in the selected version. Choose a file from the tree."
                />
              ) : fileQuery.isPending ? (
                <ContentSkeleton variant="panel" label="Loading file" />
              ) : fileQuery.isError ? (
                <ErrorState
                  title="Couldn't load this file"
                  detail="Try again to fetch the complete file."
                  onRetry={fileQuery.refetch}
                />
              ) : fileQuery.data ? (
                <FileContent
                  key={`${viewed.number}:${selectedPath}`}
                  file={fileQuery.data}
                  metadata={metadata}
                  onOpenPath={openPath}
                />
              ) : (
                <EmptyState title="File not found" />
              )}
            </Box>
          </Box>
          <Section
            title={`Checks for v${version.number}`}
            actions={
              <Button
                size="small"
                variant="text"
                aria-expanded={checksOpen}
                aria-controls="skill-version-checks"
                onClick={() => setChecksOpen((open) => !open)}
                startIcon={
                  <Iconify
                    icon={
                      checksOpen
                        ? "solar:alt-arrow-up-linear"
                        : "solar:alt-arrow-down-linear"
                    }
                    width={16}
                  />
                }
              >
                {checksOpen ? "Hide checks" : "Show checks"}
              </Button>
            }
          >
            <Collapse in={checksOpen} id="skill-version-checks">
              <ChecksList
                checks={version.checks}
                onSelect={(check) => {
                  if (check.path) openPath(check.path);
                }}
              />
            </Collapse>
          </Section>
        </>
      )}
    </Stack>
  );
}

FilesTab.propTypes = {
  skill: PropTypes.object.isRequired,
  previewEmpty: PropTypes.bool,
};
