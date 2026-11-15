import { useState } from "react";
import PropTypes from "prop-types";
import {
  Alert,
  Button,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from "@mui/material";

import { useSkillAdoption } from "src/api/skills/adoption";
import { useSkillV11Query, useSkillV11Scope } from "src/api/skills/testing";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import { DataTablePagination } from "src/components/data-table";
import EmptyState from "src/components/empty-state";
import ErrorState from "src/components/error-state";
import Section from "src/components/section";
import When from "../components/When";
import TechnicalDetails from "../components/TechnicalDetails";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { endpoints } from "src/utils/axios";
import { formatDate } from "src/utils/report-utils";
import { usePreviewState } from "src/sections/skills/components/SkillsStatePreview";
import { buildSkillTraceLink } from "src/sections/skills/utils/traces";

const timestamp = (value) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        timeZone: "UTC",
        timeZoneName: "short",
      })
    : "Ordering unavailable";
// A consumer reads as the person's key or the integration's name, never "email · api" (UX iterations 3 and 4).
const KINDS = { api: "API key", mcp: "MCP", download: "Download" };
const KIND_PREFIX = /^(api key|mcp|download)\s*[·:\-–]\s*/i;
const personName = (label) => /^[^@\s]+@[^@\s]+$/.test(label)
  ? label.split("@")[0].split(/[._-]+/).filter(Boolean).map((word) => `${word[0].toUpperCase()}${word.slice(1)}`).join(" ")
  : label;
const consumerName = (row) => {
  const keyName = typeof row.keyName === "string" ? row.keyName.trim() : "";
  if (keyName && row.channel === "api") return keyName;
  // The label sometimes already carries its kind ("API key · Maya Chen"); the kind is said once.
  const person = personName(String(row.consumerLabel || "").replace(KIND_PREFIX, "").trim()) || "Unknown consumer";
  const kind = KINDS[row.channel] || row.channel;
  if (row.channel === "api") return /key$/i.test(person) ? person : `${person}'s API key`;
  return `${person} · ${kind}`;
};
const versionList = (numbers) => {
  const names = [...new Set(numbers)].sort((a, b) => a - b).map((number) => `v${number}`);
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
};
// What a row says about the versions it fetched, in version numbers rather than a state name.
const rowSummary = (row, target) => {
  const others = row.versions.map((version) => version.version).filter((number) => number !== target.version);
  if (row.state === "current_only") return `Got v${target.version}`;
  if (row.state === "other_only") return `Still on ${versionList(others) || "another version"}`;
  if (row.state === "mixed") return versionList(row.versions.map((version) => version.version)) || "More than one version";
  if (row.state === "not_seen") return "Not seen after this move";
  if (row.state === "ambiguous") return "Ordering uncertain";
  return "Unknown observation";
};
const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
// The first line answers "is anyone still on the old version?" (UX iteration 4, LIB2); the counting caveats live in About this data.
const adoptionLead = ({ skill, target, data, days }) => {
  if (!target.promotedAt) return "Observed versions are shown without claiming adoption after this move.";
  const { currentOnly, otherOnly, mixed, notSeen, ambiguous } = data.summary;
  const stale = otherOnly + mixed;
  const rest = [
    notSeen > 0 && `${plural(notSeen, "consumer hasn't", "consumers haven't")} fetched since.`,
    ambiguous > 0 && `${plural(ambiguous, "consumer", "consumers")} can't be placed before or after the move.`,
  ].filter(Boolean);
  if (stale > 0) {
    const older = data.results.filter((row) => ["other_only", "mixed"].includes(row.state)).flatMap((row) => row.versions.map((version) => version.version)).filter((number) => number !== target.version);
    return [`${plural(stale, "consumer still received", "consumers still received")} ${versionList(older) || "another version"} after production moved.`, ...rest].join(" ");
  }
  if (currentOnly === 0) return [`No fetches since production moved to v${target.version}.`, ...rest].join(" ");
  if (rest.length === 0) return `Everyone who fetched ${skill.name} in the last ${days} days got v${target.version}.`;
  return [`${plural(currentOnly, "consumer", "consumers")} got v${target.version} in the last ${days} days.`, ...rest].join(" ");
};
const dateInput = (value) => formatDate(value).replace(" ", "T");

function TraceDialog({ versions, initialVersionId, onClose }) {
  const [projectId, setProjectId] = useState("");
  const [versionId, setVersionId] = useState(initialVersionId);
  const [windowDates, setWindowDates] = useState(() => {
    const end = new Date();
    return {
      start: dateInput(new Date(end.getTime() - 7 * 86400000)),
      end: dateInput(end),
    };
  });
  // Reuse the authorized project-picker endpoint with the Skills scoped key and
  // explicit tenant headers; the older project-list key is not tenant-specific.
  const projects = useSkillV11Query({
    parts: ["traceProjects"],
    url: () => endpoints.project.listProjects(),
    params: { project_type: "observe" },
    mapper: (result) => result.projects,
  });
  const authorized =
    !projects.isError &&
    projects.data?.some((item) => String(item.id) === projectId);
  const link =
    authorized && versions.some((item) => item.versionId === versionId)
      ? buildSkillTraceLink({
          projectId,
          versionId,
          windowStart: windowDates.start,
          windowEnd: windowDates.end,
        })
      : null;
  return (
    <ModalWrapper
      open
      title="View skill-load traces"
      subTitle="Choose a workspace project and explicit dates."
      onClose={onClose}
      onSubmit={() => {
        if (link) window.open(link, "_blank", "noopener,noreferrer");
      }}
      actionBtnTitle="Open traces"
      isValid={Boolean(link)}
    >
      <Stack spacing={2}>
        {projects.isPending && (
          <ContentSkeleton
            variant="rows"
            rows={2}
            label="Loading authorized projects"
          />
        )}
        {projects.isError && (
          <ErrorState
            title="Could not load authorized projects"
            onRetry={projects.refetch}
          />
        )}
        {projects.data?.length === 0 && (
          <EmptyState
            title="No accessible Observe projects"
            description="Choose a workspace with an Observe project to inspect runtime traces."
          />
        )}
        <TextField
          select
          label="Project"
          size="small"
          value={authorized ? projectId : ""}
          onChange={(event) => setProjectId(event.target.value)}
          disabled={projects.isPending || projects.isError}
        >
          <MenuItem value="">Choose a project</MenuItem>
          {projects.data?.map((item) => (
            <MenuItem key={item.id} value={String(item.id)}>
              {item.name}
            </MenuItem>
          ))}
        </TextField>
        <TextField
          select
          label="Immutable skill version"
          size="small"
          value={versionId}
          onChange={(event) => setVersionId(event.target.value)}
        >
          {versions.map((item) => (
            <MenuItem key={item.versionId} value={item.versionId}>
              v{item.version}
            </MenuItem>
          ))}
        </TextField>
        <Typography
          variant="s3"
          color="text.secondary"
          sx={{ overflowWrap: "anywhere" }}
        >
          {versionId}
        </Typography>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <TextField
            fullWidth
            size="small"
            type="datetime-local"
            label="From (local time)"
            value={windowDates.start}
            onChange={(event) =>
              setWindowDates((current) => ({
                ...current,
                start: event.target.value,
              }))
            }
            InputLabelProps={{ shrink: true }}
            inputProps={{ step: 1 }}
          />
          <TextField
            fullWidth
            size="small"
            type="datetime-local"
            label="To (local time)"
            value={windowDates.end}
            onChange={(event) =>
              setWindowDates((current) => ({
                ...current,
                end: event.target.value,
              }))
            }
            InputLabelProps={{ shrink: true }}
            inputProps={{ step: 1 }}
          />
        </Stack>
        {!buildSkillTraceLink({
          projectId: "validation",
          versionId,
          windowStart: windowDates.start,
          windowEnd: windowDates.end,
        }) && (
          <Alert severity="warning">
            Choose a valid start and end, with the start before the end.
          </Alert>
        )}
        <Typography variant="s3" color="text.secondary">
          Defaults to the last seven days. Requires runtime instrumentation. No
          matching traces does not prove no usage. Fetches and internal tests do
          not emit customer-load spans.
        </Typography>
      </Stack>
    </ModalWrapper>
  );
}
TraceDialog.propTypes = {
  versions: PropTypes.array.isRequired,
  initialVersionId: PropTypes.string.isRequired,
  onClose: PropTypes.func.isRequired,
};

function AdoptionContent({ skill, days: externalDays, summary, about, details }) {
  const preview = usePreviewState();
  const [localDays, setDays] = useState(7);
  const days = externalDays ?? localDays;
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [showTraces, setShowTraces] = useState(false);
  const query = useSkillAdoption(skill.id, {
    label: "production",
    days,
    page,
    pageSize,
  });
  const data = query.data;
  const target = data?.target;
  const versions = [
    ...new Map(
      [
        ...(target ? [target] : []),
        ...(data?.results.flatMap((row) => row.versions) || []),
      ].map((version) => [version.versionId, version]),
    ).values(),
  ];
  return (
    <Section
      title={
        target
          ? `Who is using v${target.version}`
          : "Production fetches"
      }
      actions={
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          flexWrap="wrap"
          useFlexGap
        >
          {externalDays == null && <TextField
            select
            size="small"
            label="Observation window"
            value={days}
            onChange={(event) => {
              setDays(Number(event.target.value));
              setPage(1);
            }}
          >
            {[7, 30, 90].map((value) => (
              <MenuItem key={value} value={value}>
                Last {value} days (UTC)
              </MenuItem>
            ))}
          </TextField>}
          <Button
            variant="outlined"
            onClick={() => setShowTraces(true)}
            disabled={!target || query.isError}
          >
            View traces
          </Button>
        </Stack>
      }
    >
      <Stack spacing={2} sx={{ minWidth: 0 }}>
        {!target && summary}
        {preview === "loading" || query.isPending ? (
          <ContentSkeleton
            variant="rows"
            rows={4}
            label="Loading observed fetches"
          />
        ) : preview === "error" || query.isError ? (
          <ErrorState
            title="Could not load observed fetches"
            detail="No version pointers have changed."
            onRetry={query.refetch}
          />
        ) : !target ? (
          <EmptyState
            title="Production is not assigned"
            description="Assign a production version to compare observed fetches after that move. This does not mean there are no users."
          />
        ) : (
          <>
            <Typography variant="s1">{adoptionLead({ skill, target, data, days })}</Typography>
            {summary}
            {preview === "empty" || data.count === 0 ? (
              <EmptyState
                title="No observed identities in this window"
                description="This is not a complete fleet inventory and does not prove no usage."
              />
            ) : (
              <>
                <TableContainer sx={{ maxWidth: "100%" }}>
                  <Table
                    size="small"
                    aria-label="Observed skill fetches after promotion"
                  >
                    <TableHead>
                      <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                        <TableCell>Consumer</TableCell>
                        <TableCell>Versions fetched</TableCell>
                        <TableCell>Last fetch</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {data.results.map((row) => (
                        <TableRow
                          hover
                          key={`${row.consumerId}:${row.channel}`}
                          sx={{ height: ROW_HEIGHT.default }}
                        >
                          <TableCell><Typography variant="s1" display="block">{consumerName(row)}</Typography><Typography variant="s3" display="block" color="text.secondary">{rowSummary(row, target)}</Typography></TableCell>
                          <TableCell>{row.versions.map((version) => <Typography key={version.versionId} variant="s3" component="div" title={version.versionId}>v{version.version} · <When value={version.lastAt} /></Typography>)}</TableCell>
                          <TableCell><When value={row.lastAt} /></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
                <DataTablePagination
                  page={page - 1}
                  pageSize={pageSize}
                  total={data.count}
                  pageSizeOptions={[10, 25, 50, 100]}
                  onPageChange={(value) => setPage(value + 1)}
                  onPageSizeChange={(value) => {
                    setPageSize(value);
                    setPage(1);
                  }}
                  disabled={query.isFetching}
                  loading={query.isFetching}
                />
              </>
            )}
          </>
        )}
        {(details || (target && data && data.count > 0 && preview !== "empty")) && <TechnicalDetails title="Counts and details">
          <Stack spacing={2} sx={{ minWidth: 0 }}>
          {target && data && data.count > 0 && preview !== "empty" && <Typography
            variant="s3"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {data.summary.currentOnly} on v{target.version} only ·{" "}
            {data.summary.otherOnly} on other versions only · {data.summary.mixed}{" "}
            on both · {data.summary.notSeen} not seen after the move ·{" "}
            {data.summary.ambiguous} ordering uncertain
          </Typography>}
          {details}
          </Stack>
        </TechnicalDetails>}
        <TechnicalDetails title="About this data">
          <Stack spacing={1}>
            {target && data && <>
              <Typography variant="s3" color="text.secondary">
                Moved {timestamp(target.promotedAt)}. Window:{" "}
                {timestamp(data.window.start)} – {timestamp(data.window.end)}. As
                of {timestamp(data.window.asOf)}.
              </Typography>
              {target.promotedAt &&
                new Date(target.promotedAt) < new Date(data.window.start) && (
                  <Typography variant="s3" color="text.secondary">
                    Within this window after promotion.
                  </Typography>
                )}
              {!target.promotedAt && (
                <Alert severity="warning">
                  The promotion order is unavailable. Observed versions are shown
                  without claiming adoption after this move.
                </Alert>
              )}
            </>}
            {target && data && <Typography variant="s3" color="text.secondary">
              Only consumers seen in this window are counted. One that hasn&apos;t fetched lately may still be
              running an older version.
            </Typography>}
            <Typography variant="s3" color="text.secondary">
              Fetches show retrieval, not installation or successful use. One key
              may represent several processes, and each channel is counted separately. Historical timestamp ordering is best
              effort; daily counts cannot establish exact post-promotion request
              totals.
            </Typography>
            {about}
          </Stack>
        </TechnicalDetails>
      </Stack>
      {showTraces && target && (
        <TraceDialog
          versions={versions}
          initialVersionId={target.versionId}
          onClose={() => setShowTraces(false)}
        />
      )}
    </Section>
  );
}
AdoptionContent.propTypes = { skill: PropTypes.object.isRequired, days: PropTypes.number, summary: PropTypes.node, about: PropTypes.node, details: PropTypes.node };

export default function AdoptionPanel({ skill, days, summary, about, details }) {
  const access = useSkillV11Scope();
  return (
    <AdoptionContent key={`${access.identity}:${skill.id}:${days ?? "local"}`} skill={skill} days={days} summary={summary} about={about} details={details} />
  );
}
AdoptionPanel.propTypes = AdoptionContent.propTypes;
