import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { CopyButton } from "./CopyCommand";
import HubPanel from "./HubPanel";

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

// True when each live model reads at most one key and every key listed
// belongs to one of them: then any one key is enough for a live run.
function anyOneKey(keys, models) {
  const live = Array.isArray(models?.live) ? models.live : [];
  const keysOf = (row) => (Array.isArray(row?.keys) ? row.keys : []);
  const modelKeys = live.map(keysOf);
  const named = new Set(modelKeys.flat());
  return (
    live.length > 0 &&
    modelKeys.every((list) => list.length <= 1) &&
    keys.every((key) => named.has(key.name))
  );
}

function KeyRow({ entry }) {
  const host = /^https:\/\//.test(entry.get || "") ? hostOf(entry.get) : null;
  return (
    <Box
      component="li"
      sx={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 0.25 }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.25 }}>
        <Typography
          component="code"
          typography="code_md"
          sx={{
            fontWeight: 600,
            color: "text.primary",
            overflowWrap: "anywhere",
          }}
        >
          {entry.name}
        </Typography>
        <CopyButton
          text={entry.name}
          label={`Copy ${entry.name}`}
          size={13}
          sx={{ p: 0.5, color: "text.secondary" }}
        />
      </Box>
      {entry.why && (
        <Typography typography="s2" color="text.secondary">
          {entry.why}
        </Typography>
      )}
      {host && (
        <Link
          href={entry.get}
          target="_blank"
          rel="nofollow noopener noreferrer"
          typography="s2"
          underline="hover"
          sx={{ color: "text.tertiary", alignSelf: "flex-start" }}
        >
          {`Get it at ${host}`}
        </Link>
      )}
    </Box>
  );
}

KeyRow.propTypes = { entry: PropTypes.object.isRequired };

// Keys (SPEC §7.2), from the manifest's keys[]: every entry runs without a
// key (R14), so the card says so first; the keys a live run reads follow,
// each with what it is for, where to get it and a copy of its exact name.
export default function KeysPanel({ keys, models }) {
  const list = Array.isArray(keys) ? keys.filter((key) => key?.name) : [];
  return (
    <HubPanel title="Keys" testId="hub-keys" dense>
      {list.length === 0 ? (
        <Typography typography="s1_2" color="text.primary">
          None. It runs without a key.
        </Typography>
      ) : (
        <>
          <Typography typography="s1_2" color="text.primary">
            None needed to try it.
          </Typography>
          <Typography typography="s2" sx={{ color: "text.tertiary" }}>
            {anyOneKey(list, models)
              ? "For a live run, any one of these."
              : "For a live run, it reads these keys."}
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              p: 0,
              listStyle: "none",
              display: "flex",
              flexDirection: "column",
              gap: 1.25,
            }}
          >
            {list.map((key) => (
              <KeyRow key={key.name} entry={key} />
            ))}
          </Box>
        </>
      )}
    </HubPanel>
  );
}

KeysPanel.propTypes = { keys: PropTypes.array, models: PropTypes.object };
