import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { GatewayFilterBar } from "../components/frame";
import { maskSecrets } from "./maskSecrets";
import { CopyButton, SettingsPanel } from "./ui";
import { GATEWAY_MONO } from "../utils/fonts";

const escapeRegExp = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// Sections the console does not offer on this gateway. The view leaves one
// out while it holds nothing ({}, [], null); one that holds something is
// shown as stored. The config itself is never changed.
const UNOFFERED_SECTIONS = ["mcp", "a2a", "tool_policy"];

const isEmptySection = (value) =>
  value == null ||
  (Array.isArray(value) && value.length === 0) ||
  (typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length === 0);

function shownConfig(config) {
  const shown = { ...(config || {}) };
  UNOFFERED_SECTIONS.forEach((key) => {
    if (key in shown && isEmptySection(shown[key])) delete shown[key];
  });
  return shown;
}

// The organization's gateway config as the gateway receives it, read-only,
// less the empty sections it does not offer (shownConfig). The config read
// masks stored org-config secrets; maskSecrets masks any other
// credential-shaped value too, so no secret is ever painted. Reading is open
// to every member, as the read endpoint is.
export default function FullConfigTab({ config }) {
  const [search, setSearch] = useState("");
  const json = useMemo(
    () => JSON.stringify(maskSecrets(shownConfig(config)), null, 2),
    [config],
  );
  const term = search.trim();
  const parts = useMemo(() => {
    if (!term) return null;
    return json.split(new RegExp(`(${escapeRegExp(term)})`, "gi"));
  }, [json, term]);
  const matches = parts ? (parts.length - 1) / 2 : 0;

  return (
    <SettingsPanel
      title="Full config"
      description="What this organization's gateway config holds, read-only. Stored secret slots, credential-named fields, header values and recognized URL credential fields are masked. Other text can contain credentials."
      actions={<CopyButton text={json} label="Copy config" />}
    >
      <GatewayFilterBar
        search={search}
        onSearchChange={setSearch}
        searchPlaceholder="Search the config"
        actions={
          term ? (
            <Typography typography="s2" color="text.secondary">
              {`${matches} match${matches === 1 ? "" : "es"}`}
            </Typography>
          ) : null
        }
      />
      <Box
        component="pre"
        data-testid="full-config-json"
        sx={{
          m: 0,
          p: 2,
          borderRadius: 1,
          bgcolor: "background.neutral",
          border: 1,
          borderColor: "divider",
          fontFamily: GATEWAY_MONO,
          typography: "s2",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          maxHeight: 600,
          overflow: "auto",
        }}
      >
        {parts
          ? parts.map((part, index) =>
              index % 2 === 1 ? (
                <Box
                  // eslint-disable-next-line react/no-array-index-key
                  key={index}
                  component="mark"
                  sx={{
                    bgcolor: "action.selected",
                    color: "text.primary",
                    borderRadius: 0.5,
                  }}
                >
                  {part}
                </Box>
              ) : (
                part
              ),
            )
          : json}
      </Box>
    </SettingsPanel>
  );
}

FullConfigTab.propTypes = { config: PropTypes.object };
