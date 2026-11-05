import PropTypes from "prop-types";
import { useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { SectionCard } from "../components/primitives";
import { allowedSitesOf, isSite, siteOf } from "../_mock/browserAgents";
import { envChangePatch } from "../_mock/versions";

/*
  The sites this environment's browser may open (T36). Part of the world, so
  a change is an environment edit like any other. Anything not listed is
  blocked in every trial and shows on the trial's Actions.
*/
export default function AllowedSitesSection({
  env,
  envState,
  patch,
  readOnly,
}) {
  const sites = allowedSitesOf(env, envState);
  const [draft, setDraft] = useState("");
  const host = siteOf(draft);
  const valid = isSite(host);
  const dup = sites.includes(host);

  const add = () => {
    if (!valid || dup) return;
    patch({
      allowedSites: [...sites, host],
      ...envChangePatch(envState, "world", `Allowed ${host}`),
    });
    setDraft("");
  };
  const remove = (s) =>
    patch({
      allowedSites: sites.filter((x) => x !== s),
      ...envChangePatch(envState, "world", `Stopped allowing ${s}`),
    });

  return (
    <SectionCard
      title={`Allowed sites (${sites.length})`}
      subtitle="The agent's browser can open only these. Anything else is blocked and shown in the trial."
      sx={{ mb: 2 }}
    >
      <Box>
        {sites.map((s) => (
          <Stack
            key={s}
            direction="row"
            alignItems="center"
            spacing={1.25}
            sx={{
              px: 2.5,
              py: 0.75,
              borderBottom: "1px solid",
              borderColor: "divider",
            }}
          >
            <Iconify
              icon="solar:global-linear"
              width={16}
              sx={{ color: "text.tertiary", flexShrink: 0 }}
            />
            <Typography
              sx={{
                typography: "s2",
                fontFamily: "ui-monospace, Menlo, monospace",
                flex: 1,
                minWidth: 0,
              }}
            >
              {s}
            </Typography>
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {s.startsWith("*.") ? "and its subdomains" : "this site only"}
            </Typography>
            {!readOnly && (
              <Tooltip arrow title="Stop allowing it">
                <IconButton size="small" onClick={() => remove(s)}>
                  <Iconify
                    icon="solar:trash-bin-trash-linear"
                    width={16}
                    sx={{ color: "text.tertiary" }}
                  />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        ))}
        {!sites.length && (
          <Typography
            sx={{ typography: "s2", color: "text.tertiary", px: 2.5, py: 1.25 }}
          >
            No sites yet — the browser can open nothing until you add one.
          </Typography>
        )}
        {!readOnly && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="flex-start"
            sx={{ px: 2.5, py: 1.25 }}
          >
            <TextField
              size="small"
              placeholder="app.example.com or *.example.com"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
              error={!!draft && (!valid || dup)}
              helperText={
                draft && !valid
                  ? "A site's address, like app.example.com"
                  : dup
                    ? "Already allowed"
                    : " "
              }
              sx={{ flex: 1, maxWidth: 420 }}
            />
            <Button
              size="small"
              variant="outlined"
              disabled={!valid || dup}
              onClick={add}
              startIcon={<Iconify icon="mingcute:add-line" width={14} />}
              sx={{ color: "text.primary", borderColor: "divider", mt: "2px" }}
            >
              Allow site
            </Button>
          </Stack>
        )}
      </Box>
    </SectionCard>
  );
}
AllowedSitesSection.propTypes = {
  env: PropTypes.object.isRequired,
  envState: PropTypes.object,
  patch: PropTypes.func,
  readOnly: PropTypes.bool,
};
