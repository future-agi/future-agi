import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Button, Menu, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SAMPLE_AGENT_DEFAULT,
  SAMPLE_AGENT_VERSIONS,
  SAMPLE_ENV_VERSIONS,
} from "src/api/simulate-environments/_fixtures/detail/versions";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import SoonChip from "../../components/SoonChip";

const num = { fontVariantNumeric: "tabular-nums" };
const mono = { fontFamily: "ui-monospace, Menlo, monospace" };

// Vel's version history: the environment's versions with Roll back and View,
// then the agent's. Our API has no environment versions, so a real
// environment lists only its agent's active version; `sample` shows Vel's.
export default function VersionHistoryMenu({
  anchor,
  onClose,
  agent,
  sample,
  onRollback,
  onView,
}) {
  return (
    <Menu
      anchorEl={anchor}
      open={!!anchor}
      onClose={onClose}
      anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      transformOrigin={{ vertical: "top", horizontal: "right" }}
      slotProps={{ paper: { sx: { width: 460, mt: 0.5 } } }}
    >
      <Box
        sx={{
          px: 2,
          pt: 1,
          pb: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s2", fontWeight: 600 }}>
            Version history
          </Typography>
          <UiOnlyFlag feature="environmentVersions" />
        </Stack>
        <Typography
          sx={{
            typography: "s3",
            color: "text.subtitle",
            whiteSpace: "normal",
          }}
        >
          Any change to the world, scenarios, evaluations, caller or credentials
          is the next version. An agent change never is.
        </Typography>
      </Box>

      <Box sx={{ px: 1, py: 1.25 }}>
        <SectionLabel
          label="Environment"
          note={sample ? `${SAMPLE_ENV_VERSIONS.length} versions` : null}
        />
        {sample ? (
          <Stack spacing={0.5}>
            {SAMPLE_ENV_VERSIONS.map((v) => (
              <EnvVersionRow
                key={v.label}
                version={v}
                onRollback={onRollback}
                onView={onView}
              />
            ))}
          </Stack>
        ) : (
          <Stack
            direction="row"
            alignItems="center"
            spacing={1}
            sx={{ px: 1, py: 0.75 }}
          >
            <SoonChip
              label="Versions"
              what="Environment versions"
              plural
              size="tiny"
            />
            <Typography
              sx={{
                typography: "s3",
                color: "text.subtitle",
                whiteSpace: "normal",
              }}
            >
              Environment versions aren&apos;t in the API yet.
            </Typography>
          </Stack>
        )}
      </Box>

      <Box
        sx={{
          px: 1,
          pt: 1.25,
          pb: 0.5,
          borderTop: "1px solid",
          borderColor: "divider",
        }}
      >
        <SectionLabel
          label="Agent"
          note={sample ? SAMPLE_AGENT_DEFAULT : null}
        />
        {sample ? (
          SAMPLE_AGENT_VERSIONS.map((a) => (
            <AgentRow
              key={a.label}
              label={`Agent ${a.label}`}
              tag={a.testing ? "testing" : null}
              meta={`${a.runs} runs · ${a.ago}`}
              note={a.note}
            />
          ))
        ) : agent?.active_version ? (
          <AgentRow
            label={`Agent ${agent.active_version}`}
            tag="active"
            meta={
              agent.versions_count
                ? `${agent.versions_count} versions in all`
                : null
            }
            note={agent.name}
          />
        ) : (
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", px: 1, py: 0.75 }}
          >
            No agent connected yet.
          </Typography>
        )}
      </Box>
    </Menu>
  );
}

VersionHistoryMenu.propTypes = {
  anchor: PropTypes.any,
  onClose: PropTypes.func,
  agent: PropTypes.shape({
    name: PropTypes.string,
    active_version: PropTypes.string,
    versions_count: PropTypes.number,
  }),
  sample: PropTypes.bool,
  onRollback: PropTypes.func,
  onView: PropTypes.func,
};

function SectionLabel({ label, note }) {
  return (
    <Stack
      direction="row"
      alignItems="baseline"
      spacing={1}
      sx={{ px: 1, mb: 0.75 }}
    >
      <Typography
        sx={{ typography: "s3", fontWeight: 600, color: "text.secondary" }}
      >
        {label}
      </Typography>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
          {note}
        </Typography>
      )}
    </Stack>
  );
}
SectionLabel.propTypes = { label: PropTypes.string, note: PropTypes.string };

function EnvVersionRow({ version, onRollback, onView }) {
  const current = version.latest;
  return (
    <Stack
      direction="row"
      spacing={1.25}
      sx={{
        px: 1,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: current ? "secondary.main" : "transparent",
        bgcolor: (t) =>
          current ? alpha(t.palette.secondary.main, 0.16) : "transparent",
      }}
    >
      {current ? (
        <Iconify
          icon="solar:check-circle-bold"
          width={16}
          sx={{ mt: "2px", flexShrink: 0, color: "text.primary" }}
        />
      ) : (
        <Box
          sx={{
            width: 14,
            height: 14,
            m: "3px 1px 0",
            flexShrink: 0,
            borderRadius: "50%",
            border: "1.5px solid",
            borderColor: "text.disabled",
          }}
        />
      )}
      <Box minWidth={0} flex={1}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ typography: "s2", fontWeight: 600, ...mono }}>
            {version.label}
          </Typography>
          {current && (
            <>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                latest
              </Typography>
              <Typography
                sx={{
                  typography: "s3",
                  color: "secondary.light",
                  fontWeight: 600,
                }}
              >
                active
              </Typography>
            </>
          )}
          <Box flex={1} />
          {!current && (
            <>
              <Button
                size="small"
                onClick={() => onRollback?.(version.label)}
                sx={{ minWidth: 0, color: "text.primary", py: 0 }}
              >
                Roll back
              </Button>
              <Button
                size="small"
                onClick={() => onView?.(version.label)}
                sx={{ minWidth: 0, color: "text.primary", py: 0 }}
              >
                View
              </Button>
            </>
          )}
        </Stack>
        <Typography
          sx={{
            typography: "s3",
            color: "text.secondary",
            whiteSpace: "normal",
          }}
        >
          {version.summary}
        </Typography>
        <Typography sx={{ typography: "s3", color: "text.subtitle", ...num }}>
          {`${version.kinds} · ${version.runs} runs · ${version.ago}`}
        </Typography>
      </Box>
    </Stack>
  );
}
EnvVersionRow.propTypes = {
  version: PropTypes.object,
  onRollback: PropTypes.func,
  onView: PropTypes.func,
};

function AgentRow({ label, tag, meta, note }) {
  return (
    <Box sx={{ px: 1, py: 0.75 }}>
      <Stack direction="row" alignItems="baseline" spacing={0.75}>
        <Typography sx={{ typography: "s2", fontWeight: 600, ...mono }}>
          {label}
        </Typography>
        {tag && (
          <Typography
            sx={{ typography: "s3", color: "secondary.light", fontWeight: 600 }}
          >
            {tag}
          </Typography>
        )}
        <Box flex={1} />
        {meta && (
          <Typography sx={{ typography: "s3", color: "text.subtitle", ...num }}>
            {meta}
          </Typography>
        )}
      </Stack>
      {note && (
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          {note}
        </Typography>
      )}
    </Box>
  );
}
AgentRow.propTypes = {
  label: PropTypes.string,
  tag: PropTypes.string,
  meta: PropTypes.string,
  note: PropTypes.string,
};
