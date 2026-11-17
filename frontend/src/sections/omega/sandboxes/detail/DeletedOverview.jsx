import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import { useNavigate } from "react-router-dom";

import { useActivity } from "../api/queries";
import SandboxIcon from "../components/SandboxIcon";
import Section, { Facts } from "../components/Section";
import {
  EMPTY,
  formatCpus,
  formatDateTime,
  formatMemoryMb,
  timeAgo,
} from "../lib/format";
import { sandboxPath } from "../lib/links";
import useNow from "../lib/useNow";

const mono = { fontFamily: "monospace", fontSize: 13 };

/**
 * When it was deleted: the audited `sandbox.destroy` line when Activity has
 * it, else the record's last update (EE moves `updatedAt` on every change).
 */
export function deletedAt(sandbox, activity) {
  const line = (activity || []).find(
    (entry) => entry.verb === "sandbox.destroy" && entry.outcome === "ok",
  );
  return line?.at || sandbox.updatedAt || null;
}

/**
 * Overview of a deleted sandbox: what it was and when it went, and nothing
 * that implies it still runs (no isolation statement, idle timeout or
 * hibernation clock). Its record and Activity are all that is left.
 */
export default function DeletedOverview({ sandbox, templateName, sizeLabel }) {
  const navigate = useNavigate();
  const now = useNow();
  const activity = useActivity(sandbox.id);
  const when = deletedAt(sandbox, activity.data);
  return (
    <Box
      data-testid="sandbox-deleted-overview"
      sx={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}
    >
      <Box
        role="status"
        sx={{
          display: "flex",
          gap: 1.5,
          alignItems: "flex-start",
          flexDirection: { xs: "column", sm: "row" },
          justifyContent: "space-between",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          bgcolor: "background.neutral",
          px: 2,
          py: 1.5,
        }}
      >
        <Box
          sx={{
            display: "flex",
            gap: 1.5,
            alignItems: "flex-start",
            minWidth: 0,
          }}
        >
          <SandboxIcon
            name="trash"
            size={20}
            sx={{ mt: 0.25, color: "text.secondary" }}
          />
          <Box sx={{ minWidth: 0 }}>
            <Typography
              typography="s1"
              fontWeight="fontWeightMedium"
              color="text.primary"
              data-testid="sandbox-deleted-title"
            >
              {when
                ? `This sandbox was deleted ${timeAgo(when, now)}`
                : "This sandbox was deleted"}
            </Typography>
            <Typography typography="s2" color="text.secondary">
              Its container and files were removed, so nothing can run in it.
              This record and its Activity are kept.
            </Typography>
          </Box>
        </Box>
        <Button
          size="small"
          variant="outlined"
          color="inherit"
          sx={{ flexShrink: 0 }}
          onClick={() => navigate(sandboxPath(sandbox.id, "activity"))}
          data-testid="sandbox-deleted-activity"
        >
          Open Activity
        </Button>
      </Box>
      <Section title="What it was">
        <Facts
          rows={[
            ...(sandbox.template
              ? [{ label: "Template", value: templateName }]
              : []),
            ...(sandbox.size ? [{ label: "Size", value: sizeLabel }] : []),
            {
              label: "Image",
              value: sandbox.image ? (
                <Box component="span" sx={mono}>
                  {sandbox.image}
                </Box>
              ) : (
                EMPTY
              ),
            },
            {
              label: "CPU and memory",
              value: sandbox.limits
                ? `${formatCpus(sandbox.limits.cpus)} · ${formatMemoryMb(sandbox.limits.memoryMb)}`
                : EMPTY,
            },
            { label: "Created", value: formatDateTime(sandbox.createdAt) },
            {
              label: "Deleted",
              value: formatDateTime(when),
              testId: "overview-deleted-at",
            },
            {
              label: "Commands run",
              value: String(sandbox.meter?.execCount ?? 0),
            },
          ]}
        />
      </Section>
    </Box>
  );
}

DeletedOverview.propTypes = {
  sandbox: PropTypes.object.isRequired,
  templateName: PropTypes.string,
  sizeLabel: PropTypes.string,
};
