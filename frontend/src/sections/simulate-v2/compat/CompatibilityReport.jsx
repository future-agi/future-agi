import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { StatusDot } from "../components/primitives";
import CopyCommandButton from "../components/CopyCommandButton";
import { TextLink } from "../agents/connect/connectBits";
import { clickable, focusRing } from "../components/clickable";
import { COMPAT_STATUS, doctorCli } from "../_mock/compatibility";

const MONO = "ui-monospace, Menlo, monospace";
const COLS = "200px 112px minmax(0, 1fr) 96px 104px";

const when = (iso) =>
  iso
    ? new Date(iso).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;

/* One capability: status, what we found, the cases it touches, the way forward. */
function CapabilityRow({ row, titles, onRepair, onRetest }) {
  const [open, setOpen] = useState(false);
  const meta = COMPAT_STATUS[row.status];
  const n = row.affected.length;
  return (
    <Box sx={{ "& + &": { borderTop: "1px solid", borderColor: "divider" } }}>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: COLS,
          columnGap: 2,
          alignItems: "start",
          px: 1.5,
          py: 0.875,
        }}
      >
        <Typography sx={{ typography: "s2" }}>{row.label}</Typography>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <StatusDot status={meta.dot} size={6} live={false} />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            {meta.label}
          </Typography>
        </Stack>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
        >
          {row.detail}
        </Typography>
        {n ? (
          <Box
            {...clickable(() => setOpen((o) => !o), { expanded: open })}
            sx={{
              ...focusRing,
              cursor: "pointer",
              typography: "s2",
              fontVariantNumeric: "tabular-nums",
              textAlign: "right",
              "&:hover": { textDecoration: "underline" },
            }}
          >
            {`${n} case${n === 1 ? "" : "s"}`}
          </Box>
        ) : (
          <Typography
            sx={{
              typography: "s2",
              color: "text.disabled",
              textAlign: "right",
            }}
          >
            —
          </Typography>
        )}
        <Box sx={{ textAlign: "right" }}>
          {row.repair === "agent" && onRepair && (
            <TextLink onClick={() => onRepair(row)}>Add the tools</TextLink>
          )}
          {row.repair === "connection" && onRepair && (
            <TextLink onClick={() => onRepair(row)}>Repair</TextLink>
          )}
          {row.repair === "test" && onRetest && (
            <TextLink onClick={() => onRetest(row)}>
              {row.status === "untested" ? "Run the check" : "Test again"}
            </TextLink>
          )}
        </Box>
      </Box>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ px: 1.5, pb: 1, pl: "calc(200px + 112px + 44px)" }}>
          {row.affected.map((id) => (
            <Typography
              key={id}
              noWrap
              sx={{ typography: "s3", color: "text.tertiary" }}
            >
              <Box component="span" sx={{ fontFamily: MONO, mr: 1 }}>
                {id}
              </Box>
              {titles[id] || ""}
            </Typography>
          ))}
        </Box>
      </Collapse>
    </Box>
  );
}
CapabilityRow.propTypes = {
  row: PropTypes.object.isRequired,
  titles: PropTypes.object,
  onRepair: PropTypes.func,
  onRetest: PropTypes.func,
};

/**
 * The compatibility report for one agent on one world (roadmap release.md):
 * exactly which versions it's about, the requested / eligible / blocked
 * scope, then one row per capability — its status, what we found, the
 * cases it affects and the repair. Reachable isn't isolated, isolated isn't
 * observable, observable isn't gradable: each is its own row.
 */
export default function CompatibilityReport({
  report,
  agent,
  env,
  titles = {},
  onRepair,
  onRetest,
}) {
  const { identity: id, scope } = report;
  return (
    <Stack spacing={1.5}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
      >
        <Typography
          sx={{
            typography: "s2",
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {[
            `${id.agent} ${id.agentVersion}`,
            id.connection,
            `× ${id.world} ${id.worldVersion}${id.release ? ` · release ${id.release}` : ""}`,
            id.mode,
            id.family
              ? `family ${id.family} (${id.familyStatus.toLowerCase()} — context, not proof)`
              : "family not in the tested list",
            report.testedAt
              ? `checked ${when(report.testedAt)}`
              : "not checked yet",
          ]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
        <CopyCommandButton
          command={doctorCli(agent, env)}
          label="Copy doctor command"
        />
      </Stack>

      {/* The scope, before anything is run: requested, eligible, blocked. */}
      <Stack
        direction="row"
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
        }}
      >
        {[
          ["Requested", scope.requested],
          ["Eligible", scope.eligible],
          ["Blocked", scope.blocked.length],
        ].map(([k, n], i) => (
          <Box
            key={k}
            sx={{
              flex: 1,
              px: 1.5,
              py: 1,
              ...(i && { borderLeft: "1px solid", borderColor: "divider" }),
            }}
          >
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {k}
            </Typography>
            <Typography
              sx={{
                typography: "m3",
                fontWeight: 600,
                fontVariantNumeric: "tabular-nums",
                color:
                  k === "Blocked" && n ? "status.danger.fg" : "text.primary",
              }}
            >
              {n}
            </Typography>
          </Box>
        ))}
      </Stack>
      {report.staleWhy && (
        <Typography sx={{ typography: "s2", color: "status.warning.fg" }}>
          {`${report.staleWhy} — the affected checks are stale until tested again. Earlier reports stay with the runs they were made for.`}
        </Typography>
      )}

      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          overflowX: "auto",
        }}
      >
        <Box sx={{ minWidth: 720 }}>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: COLS,
              columnGap: 2,
              px: 1.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            {["Capability", "Status", "What we found", "Affects", ""].map(
              (h, i) => (
                <Typography
                  key={h || i}
                  sx={{
                    typography: "s3",
                    color: "text.tertiary",
                    textAlign: i >= 3 ? "right" : "left",
                  }}
                >
                  {h}
                </Typography>
              ),
            )}
          </Box>
          {report.rows.map((r) => (
            <CapabilityRow
              key={r.id}
              row={r}
              titles={titles}
              onRepair={onRepair}
              onRetest={onRetest}
            />
          ))}
        </Box>
      </Box>
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Iconify
          icon="solar:info-circle-linear"
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          {`Each check proves its own capability on this configuration, not every task.${report.notNeeded.length ? ` Not needed by this world: ${report.notNeeded.join(", ").toLowerCase()}.` : ""}`}
        </Typography>
      </Stack>
    </Stack>
  );
}
CompatibilityReport.propTypes = {
  report: PropTypes.object.isRequired,
  agent: PropTypes.object,
  env: PropTypes.object,
  /* Scenario titles by id, for the affected-case lists. */
  titles: PropTypes.object,
  onRepair: PropTypes.func,
  onRetest: PropTypes.func,
};
