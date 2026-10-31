import PropTypes from "prop-types";
import { useState } from "react";
import { enqueueSnackbar } from "notistack";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SoonChip from "../../components/SoonChip";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";
import SettingsCard from "./SettingsCard";
import { MAX_EGRESS_DOMAINS, egressError, parseEgress } from "./secretRefs";
import { DIVIDER_SX, MONO } from "./settings.constants";

// The public hosts the sandbox lets the agent reach while it runs. The list is
// real (`settings.security.allowed_egress_domains`); changing it has no API
// yet, so Allow and Remove send nothing and the list stays as it is.
export default function EgressCard({ domains, disabled = false }) {
  const [text, setText] = useState("");
  const [error, setError] = useState(null);
  const known = Array.isArray(domains);
  const list = known ? domains : [];

  const allow = () => {
    const asked = parseEgress(text).filter((d) => !list.includes(d));
    if (!asked.length) {
      setText("");
      return;
    }
    const problem = egressError([...list, ...asked]);
    if (problem) {
      setError(problem);
      return;
    }
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
    setText("");
    setError(null);
  };

  return (
    <SettingsCard
      label="Egress"
      title={known ? `Egress · ${list.length}` : "Egress"}
      subtitle={`Public hostnames the agent may reach while it runs, such as hardcoded APIs or TURN endpoints. Everything else is firewalled at the sandbox. Up to ${MAX_EGRESS_DOMAINS}.`}
      action={
        <Stack direction="row" alignItems="center" spacing={1}>
          {!known && (
            <SoonChip label="Allowed domains" what="The egress allow-list" />
          )}
          <UiOnlyFlag feature="environmentSettings" />
        </Stack>
      }
    >
      <Stack divider={<Box sx={DIVIDER_SX} />}>
        {known && list.length === 0 && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.subtitle",
              px: 2.5,
              py: 1.375,
            }}
          >
            None. The agent can only reach what the world answers.
          </Typography>
        )}
        {list.map((d) => (
          <Stack
            key={d}
            direction="row"
            alignItems="center"
            spacing={1.5}
            sx={{ px: 2.5, py: 1 }}
          >
            <Iconify
              icon="solar:global-linear"
              width={14}
              sx={{ color: "text.subtitle", flexShrink: 0 }}
            />
            <Typography
              sx={{ typography: "s2", fontFamily: MONO, flex: 1, minWidth: 0 }}
            >
              {d}
            </Typography>
            <IconButton
              size="small"
              aria-label={`Remove ${d}`}
              disabled={disabled}
              onClick={() =>
                enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" })
              }
            >
              <Iconify
                icon="solar:trash-bin-trash-linear"
                width={16}
                sx={{ color: "text.subtitle" }}
              />
            </IconButton>
          </Stack>
        ))}
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1}
          sx={{ px: 2.5, py: 1.25 }}
        >
          <Box flex={1} minWidth={0}>
            <TextField
              fullWidth
              size="small"
              value={text}
              disabled={disabled}
              onChange={(e) => {
                setText(e.target.value);
                setError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  allow();
                }
              }}
              placeholder="api.example.com, turn.example.com"
              error={!!error}
              inputProps={{
                spellCheck: false,
                "aria-label": "Add egress domains",
              }}
              sx={{
                "& .MuiInputBase-input": { typography: "s2", fontFamily: MONO },
              }}
            />
            {error && (
              <Typography
                role="alert"
                sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
              >
                {error}
              </Typography>
            )}
          </Box>
          <Button
            size="small"
            variant="outlined"
            disabled={disabled || !text.trim()}
            onClick={allow}
            sx={{
              color: "text.primary",
              borderColor: "divider",
              flexShrink: 0,
              mt: 0.25,
            }}
          >
            Allow
          </Button>
        </Stack>
      </Stack>
    </SettingsCard>
  );
}
EgressCard.propTypes = {
  domains: PropTypes.arrayOf(PropTypes.string),
  disabled: PropTypes.bool,
};
