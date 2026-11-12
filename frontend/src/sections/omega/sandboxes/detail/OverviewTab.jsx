import React, { useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useSnackbar } from "notistack";

import { RouterLink } from "src/routes/components";

import { useSandboxMutation } from "../api/queries";
import Section, { Facts } from "../components/Section";
import { ttlOptions } from "../lib/createForm";
import {
  EMPTY,
  formatCpus,
  formatDateTime,
  formatDuration,
  formatMemoryMb,
  hibernatesIn,
  networkLabel,
  runtimeLabel,
  timeAgo,
} from "../lib/format";
import { agentHref, runHref, sandboxPath } from "../lib/links";
import { timeLimits } from "../lib/runOptions";
import useNow from "../lib/useNow";
import CommandLimit from "./CommandLimit";
import DeletedOverview from "./DeletedOverview";

const mono = { fontFamily: "monospace", fontSize: 13 };

/** A template's display name from the caps (its id when the caps do not list it). */
function templateName(caps, id) {
  return caps?.templates?.find((template) => template.id === id)?.name || id;
}

/** "Medium" for a size id (the id itself when the caps do not list it). */
function sizeLabel(caps, id) {
  return caps?.sizes?.find((size) => size.id === id)?.label || id;
}

function IdleTimeout({ sandbox, caps }) {
  const [editing, setEditing] = useState(false);
  const { enqueueSnackbar } = useSnackbar();
  const update = useSandboxMutation(
    (api, seconds) => api.update(sandbox.id, { idleTtlSeconds: seconds }),
    {
      onSuccess: () => setEditing(false),
      onError: (error) => enqueueSnackbar(error.message, { variant: "error" }),
    },
  );
  if (sandbox.idleTtlSeconds === null) return "Set by the run that owns it";
  if (!editing || !caps) {
    return (
      <Box
        component="span"
        sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}
      >
        {formatDuration(sandbox.idleTtlSeconds)} idle
        {!sandbox.readOnly && caps && sandbox.state !== "destroyed" && (
          <Button
            size="small"
            onClick={() => setEditing(true)}
            sx={{ minWidth: 0, p: 0, typography: "s2" }}
            data-testid="ttl-edit"
          >
            Change
          </Button>
        )}
      </Box>
    );
  }
  return (
    <TextField
      select
      size="small"
      value={sandbox.idleTtlSeconds}
      disabled={update.isPending}
      onChange={(event) => update.mutate(Number(event.target.value))}
      sx={{ minWidth: 180 }}
      inputProps={{ "data-testid": "ttl-select" }}
    >
      {[...new Set([...ttlOptions(caps), sandbox.idleTtlSeconds])]
        .sort((a, b) => a - b)
        .map((seconds) => (
          <MenuItem key={seconds} value={seconds}>
            {formatDuration(seconds)} idle
          </MenuItem>
        ))}
    </TextField>
  );
}

function UsedBy({ uses }) {
  return (
    <Box
      component="ul"
      sx={{
        listStyle: "none",
        m: 0,
        p: 0,
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      {uses.map((use) => (
        <Box
          component="li"
          key={`${use.runId}:${use.taskId}`}
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 1,
            alignItems: "baseline",
          }}
        >
          <Link
            component={RouterLink}
            href={runHref(use.runId)}
            typography="s1"
            data-testid="used-by-run"
          >
            {use.title || use.runId}
          </Link>
          <Typography typography="s2" color="text.secondary" sx={mono}>
            {use.runId}
          </Typography>
          {use.agentId && (
            <Link
              component={RouterLink}
              href={agentHref(use.agentId)}
              typography="s2"
              color="text.secondary"
            >
              Agent {use.agentId}
            </Link>
          )}
        </Box>
      ))}
    </Box>
  );
}

UsedBy.propTypes = { uses: PropTypes.array.isRequired };

export default function OverviewTab({ sandbox, caps }) {
  const now = useNow();
  const { provider } = sandbox;
  if (sandbox.state === "destroyed") {
    return (
      <DeletedOverview
        sandbox={sandbox}
        templateName={templateName(caps, sandbox.template)}
        sizeLabel={sizeLabel(caps, sandbox.size)}
      />
    );
  }
  const lifecycle = [
    { label: "Created", value: formatDateTime(sandbox.createdAt) },
    { label: "Last activity", value: timeAgo(sandbox.lastActivityAt, now) },
    {
      label: "Hibernates",
      value:
        sandbox.state === "active"
          ? hibernatesIn(sandbox.hibernatesAt, now)
          : EMPTY,
      testId: "overview-hibernates",
    },
    {
      label: "Idle timeout",
      value: <IdleTimeout sandbox={sandbox} caps={caps} />,
    },
    {
      label: "Commands run",
      value: String(sandbox.meter.execCount),
      testId: "overview-exec-count",
    },
    {
      label: "Last command",
      value: sandbox.meter.lastExecAt
        ? timeAgo(sandbox.meter.lastExecAt, now)
        : EMPTY,
    },
  ];
  if (sandbox.restoredFrom) {
    lifecycle.push({
      label: "Restored from",
      value: (
        <Link
          component={RouterLink}
          href={sandboxPath(sandbox.restoredFrom.sandboxId, "snapshots")}
          sx={mono}
        >
          {sandbox.restoredFrom.snapshotId}
        </Link>
      ),
    });
  }

  return (
    <Box
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", lg: "3fr 2fr" },
        gap: 2,
        alignItems: "start",
      }}
    >
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
      >
        <Section
          title="Isolation"
          caption={runtimeLabel(provider.kind)}
          testId="overview-isolation"
        >
          <Typography
            typography="s1"
            color="text.primary"
            data-testid="sandbox-isolation-statement"
          >
            {provider.isolationStatement}
          </Typography>
        </Section>
        {/* Only agent-run sandboxes carry runs today, and those leave the list
            when their run ends: an always-empty panel is hidden. */}
        {sandbox.usedBy?.length > 0 && (
          <Section
            title="Used by"
            caption="Agent runs in this workspace that ran in this sandbox."
            testId="overview-used-by"
          >
            <UsedBy uses={sandbox.usedBy} />
          </Section>
        )}
      </Box>
      <Box
        sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
      >
        <Section title="Resources">
          <Facts
            rows={[
              ...(sandbox.template
                ? [
                    {
                      label: "Template",
                      value: templateName(caps, sandbox.template),
                      testId: "overview-template",
                    },
                  ]
                : []),
              ...(sandbox.size
                ? [
                    {
                      label: "Size",
                      value: sizeLabel(caps, sandbox.size),
                      testId: "overview-size",
                    },
                  ]
                : []),
              {
                label: "Image",
                value: sandbox.image ? (
                  <Box
                    component="span"
                    sx={mono}
                    title={sandbox.imageId || undefined}
                  >
                    {sandbox.image}
                  </Box>
                ) : (
                  EMPTY
                ),
              },
              {
                label: "CPU",
                value: sandbox.limits ? formatCpus(sandbox.limits.cpus) : EMPTY,
              },
              {
                label: "Memory",
                value: sandbox.limits
                  ? formatMemoryMb(sandbox.limits.memoryMb)
                  : EMPTY,
              },
              {
                label: "Processes",
                value: sandbox.limits
                  ? String(sandbox.limits.pidsLimit)
                  : EMPTY,
              },
              {
                label: "Network",
                value: networkLabel(sandbox.network),
                testId: "overview-network",
              },
              ...(timeLimits(caps)
                ? [
                    {
                      label: "Command time limit",
                      value: <CommandLimit caps={caps} fact />,
                      testId: "overview-command-limit",
                    },
                  ]
                : []),
            ]}
          />
        </Section>
        <Section title="Lifecycle">
          <Facts rows={lifecycle} />
        </Section>
      </Box>
    </Box>
  );
}

OverviewTab.propTypes = {
  sandbox: PropTypes.object.isRequired,
  caps: PropTypes.object,
};

IdleTimeout.propTypes = {
  sandbox: PropTypes.object.isRequired,
  caps: PropTypes.object,
};
