import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Typography from "@mui/material/Typography";

import { HubSection } from "./HubStates";

// "Keys needed" (SPEC §7.2): from the manifest's keys[], with why and where
// to get each. Every entry runs without a key (R14).
export default function KeysPanel({ keys }) {
  const list = Array.isArray(keys) ? keys : [];
  return (
    <HubSection title="Keys needed" testId="hub-keys">
      {list.length === 0 ? (
        <Typography typography="s2" color="text.secondary">
          None. It runs without a key.
        </Typography>
      ) : (
        <Box
          component="ul"
          sx={{
            m: 0,
            p: 0,
            listStyle: "none",
            display: "flex",
            flexDirection: "column",
            gap: 1,
          }}
        >
          {list.map((key) => (
            <Box component="li" key={key.name} sx={{ minWidth: 0 }}>
              <Typography
                component="code"
                typography="s2"
                sx={{
                  fontFamily: "monospace",
                  fontWeight: 600,
                  color: "text.primary",
                  wordBreak: "break-all",
                }}
              >
                {key.name}
              </Typography>
              {key.for === "live" && (
                <Typography
                  component="span"
                  typography="s3"
                  color="text.tertiary"
                  sx={{ ml: 0.75 }}
                >
                  live runs only
                </Typography>
              )}
              {key.why && (
                <Typography typography="s2" color="text.secondary">
                  {key.why}
                </Typography>
              )}
              {key.get && /^https:\/\//.test(key.get) && (
                <Link
                  href={key.get}
                  target="_blank"
                  rel="nofollow noopener noreferrer"
                  typography="s2"
                >
                  Get this key
                </Link>
              )}
            </Box>
          ))}
          <Typography typography="s3" color="text.tertiary">
            Every entry also runs without a key, on a scripted model.
          </Typography>
        </Box>
      )}
    </HubSection>
  );
}

KeysPanel.propTypes = { keys: PropTypes.array };
