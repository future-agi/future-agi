import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Collapse,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyCommandButton from "../../components/CopyCommandButton";
import { clickable, focusRing } from "../../components/clickable";
import { versioningOf } from "../../_mock/platformPreview";

const MONO = "ui-monospace, Menlo, monospace";
const row = {
  px: 1.25,
  py: 0.75,
  "& + &": { borderTop: "1px solid", borderColor: "divider" },
};

/**
 * After picking a platform agent: which version a run is pinned to (or that
 * the platform has none), then a read-only preview of what we'll read —
 * prompt, tools, channel — with each tool marked redirected to the test
 * world or still running for real. Nothing here changes the agent.
 */
export function PlatformVersionPicker({ provider, versions, value, onChange }) {
  const v = versioningOf(provider);
  if (!versions.length)
    return (
      <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
        {v.note}
      </Typography>
    );
  return (
    <Stack direction="row" alignItems="center" spacing={1.25}>
      <Typography sx={{ typography: "s3", fontWeight: 600 }}>
        Version
      </Typography>
      <Select
        size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        inputProps={{ "aria-label": "Version" }}
        sx={{
          typography: "s2",
          minWidth: 220,
          "& .MuiSelect-select": { py: 0.5 },
        }}
      >
        {versions.map((x) => (
          <MenuItem key={x.id} value={x.id} sx={{ typography: "s2" }}>
            {`${x.label} · ${x.note}`}
          </MenuItem>
        ))}
      </Select>
      <Typography
        sx={{ typography: "s3", color: "text.subtitle", flex: 1, minWidth: 0 }}
      >
        {versions.find((x) => x.id === value)?.published === false
          ? "A draft can change between runs — results are marked unpinned."
          : v.note}
      </Typography>
    </Stack>
  );
}
PlatformVersionPicker.propTypes = {
  provider: PropTypes.string,
  versions: PropTypes.array.isRequired,
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};

export default function PlatformPreview({ preview, cli }) {
  const [open, setOpen] = useState(false);
  if (!preview) return null;
  const live = preview.tools.filter((t) => t.handling === "live");
  const risky = live.filter((t) => t.risky);
  return (
    <Box sx={{ border: "1px solid", borderColor: "divider", borderRadius: 1 }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        {...clickable(() => setOpen((o) => !o), { expanded: open })}
        sx={{ ...focusRing, px: 1.25, py: 0.875, cursor: "pointer" }}
      >
        <Iconify
          icon={
            open
              ? "solar:alt-arrow-down-linear"
              : "solar:alt-arrow-right-linear"
          }
          width={14}
          sx={{ color: "text.tertiary" }}
        />
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          What we&apos;ll read
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {`prompt · ${preview.tools.length} tools · ${preview.channel}${risky.length ? ` · ${risky.map((t) => t.name).join(", ")} runs for real` : ""}`}
        </Typography>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Stack spacing={1.25} sx={{ px: 1.25, pb: 1.25 }}>
          <Box>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                mb: 0.25,
              }}
            >
              Prompt
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {preview.prompt}
            </Typography>
          </Box>
          <Box>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                mb: 0.25,
              }}
            >
              Tools — what a test does with each
            </Typography>
            <Box
              sx={{
                border: "1px solid",
                borderColor: "divider",
                borderRadius: 1,
              }}
            >
              {preview.tools.map((t) => (
                <Stack
                  key={t.name}
                  direction="row"
                  spacing={1.25}
                  alignItems="baseline"
                  sx={row}
                >
                  <Typography
                    sx={{
                      typography: "s2",
                      fontFamily: MONO,
                      width: 170,
                      flexShrink: 0,
                    }}
                  >
                    {t.name}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      fontWeight: 600,
                      width: 150,
                      flexShrink: 0,
                      color:
                        t.handling === "live" && t.risky
                          ? "status.warning.fg"
                          : "text.secondary",
                    }}
                  >
                    {t.handling === "live"
                      ? "Runs for real"
                      : "Redirected to the test world"}
                  </Typography>
                  <Typography
                    sx={{
                      typography: "s3",
                      color: "text.tertiary",
                      flex: 1,
                      minWidth: 0,
                    }}
                  >
                    {`${t.kind === "native" ? "Platform action — " : ""}${t.line}`}
                  </Typography>
                </Stack>
              ))}
            </Box>
            {live.length > 0 && (
              <Typography
                sx={{ typography: "s3", color: "text.subtitle", mt: 0.5 }}
              >
                {`Platform actions don't go through your server, so we can't redirect them.${risky.length ? " A test that reaches one asks first, with the destination named." : ""}`}
              </Typography>
            )}
          </Box>
          <Box>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                mb: 0.25,
              }}
            >
              Channel
            </Typography>
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {preview.channel}
            </Typography>
          </Box>
          {cli && (
            <Box>
              <CopyCommandButton command={cli} label="Copy as command" />
            </Box>
          )}
        </Stack>
      </Collapse>
    </Box>
  );
}
PlatformPreview.propTypes = {
  preview: PropTypes.object,
  cli: PropTypes.string,
};
