import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SideDrawer from "../../components/SideDrawer";
import { MAPPING_NOTE } from "./evalMapping";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * An added eval's mapping — the pencil next to the bin.
 *
 * PRD v6: in v0 the variable mapping is shown and removable, not editable.
 * The environment sets it from what a run produces; a hand-edited mapping
 * could point a variable at a column the run never fills. So each variable →
 * column pair is read-only with a Remove, and the eval library's own config
 * screen — which edits mapping and config together — is no longer opened
 * from here.
 *
 * Removals are staged and saved together, so a slip is one Undo away.
 */
export default function EditEvalDrawer({
  item,
  onClose,
  onSave,
  locked = false,
}) {
  const entries = Object.entries(item.mapping || {});
  const [removed, setRemoved] = useState(() => new Set());
  const toggle = (variable) =>
    setRemoved((prev) => {
      const next = new Set(prev);
      if (next.has(variable)) next.delete(variable);
      else next.add(variable);
      return next;
    });
  const save = () => {
    onSave({
      mapping: Object.fromEntries(entries.filter(([v]) => !removed.has(v))),
    });
    onClose();
  };

  return (
    <SideDrawer open onClose={onClose} width={480}>
      <Stack sx={{ height: "100%" }}>
        <Stack
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            px: 2.5,
            py: 2,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} minWidth={0}>
            <Typography sx={{ typography: "m2", fontWeight: 600 }}>
              {item.instanceName || item.name}
            </Typography>
            {item.blurb && (
              <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
                {item.blurb}
              </Typography>
            )}
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="Close">
            <Iconify
              icon="solar:close-circle-linear"
              width={20}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Stack>

        <Stack spacing={2.5} sx={{ p: 2.5, flex: 1, overflowY: "auto" }}>
          <Box>
            <Typography
              sx={{
                typography: "s3",
                fontWeight: 600,
                color: "text.tertiary",
                textTransform: "none",
                letterSpacing: 0.4,
                mb: 0.5,
              }}
            >
              Variable mapping
            </Typography>
            <Typography
              sx={{ typography: "s3", color: "text.tertiary", mb: 1.25 }}
            >
              {MAPPING_NOTE}
            </Typography>
            {entries.length === 0 ? (
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                Nothing mapped — this evaluation reads the run as a whole.
              </Typography>
            ) : (
              <Box
                sx={{
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: 1,
                }}
              >
                <Stack
                  divider={
                    <Box
                      sx={{ borderBottom: "1px solid", borderColor: "divider" }}
                    />
                  }
                >
                  {entries.map(([variable, column]) => {
                    const gone = removed.has(variable);
                    return (
                      <Stack
                        key={variable}
                        direction="row"
                        alignItems="center"
                        spacing={1}
                        sx={{ pl: 1.5, pr: 0.75, py: 0.75 }}
                      >
                        <Typography
                          noWrap
                          sx={{
                            typography: "s3",
                            fontFamily: MONO,
                            color: gone ? "text.disabled" : "text.secondary",
                            textDecoration: gone ? "line-through" : "none",
                          }}
                        >
                          {variable}
                        </Typography>
                        <Iconify
                          icon="solar:arrow-right-linear"
                          width={12}
                          sx={{ color: "text.disabled", flexShrink: 0 }}
                        />
                        <Typography
                          noWrap
                          sx={{
                            typography: "s3",
                            fontFamily: MONO,
                            flex: 1,
                            minWidth: 0,
                            color: gone ? "text.disabled" : "text.primary",
                            textDecoration: gone ? "line-through" : "none",
                          }}
                        >
                          {String(column)}
                        </Typography>
                        {!locked && (
                          <Button
                            size="small"
                            onClick={() => toggle(variable)}
                            sx={{ color: "text.secondary", minWidth: 0 }}
                          >
                            {gone ? "Undo" : "Remove"}
                          </Button>
                        )}
                      </Stack>
                    );
                  })}
                </Stack>
              </Box>
            )}
          </Box>

          {(item.threshold != null || item.model) && (
            <Box>
              <Typography
                sx={{
                  typography: "s3",
                  fontWeight: 600,
                  color: "text.tertiary",
                  textTransform: "none",
                  letterSpacing: 0.4,
                  mb: 0.75,
                }}
              >
                Scoring
              </Typography>
              <Typography
                sx={{
                  typography: "s2",
                  color: "text.secondary",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {[
                  item.threshold != null &&
                    `Pass ≥ ${(item.threshold * 100).toFixed(0)}%`,
                  item.model && `judged by ${item.model}`,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </Typography>
            </Box>
          )}
        </Stack>

        <Stack
          direction="row"
          spacing={1.5}
          sx={{
            px: 2.5,
            py: 2,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box flex={1} />
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            {removed.size ? "Cancel" : "Close"}
          </Button>
          {entries.length > 0 && !locked && (
            <Tooltip
              arrow
              title={removed.size ? "" : "Remove a mapping to save"}
            >
              <span>
                <Button
                  size="small"
                  variant="contained"
                  color="primary"
                  disabled={!removed.size}
                  onClick={save}
                >
                  {removed.size
                    ? `Remove ${removed.size} mapping${removed.size === 1 ? "" : "s"}`
                    : "Save"}
                </Button>
              </span>
            </Tooltip>
          )}
        </Stack>
      </Stack>
    </SideDrawer>
  );
}

EditEvalDrawer.propTypes = {
  item: PropTypes.object.isRequired,
  env: PropTypes.object,
  envState: PropTypes.object,
  onClose: PropTypes.func.isRequired,
  onSave: PropTypes.func.isRequired,
  locked: PropTypes.bool,
};
