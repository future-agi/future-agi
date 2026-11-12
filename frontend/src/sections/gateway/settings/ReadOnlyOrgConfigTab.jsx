import React, { useId } from "react";
import PropTypes from "prop-types";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { isStored } from "./ui/storedValues";
import { GATEWAY_MONO } from "../utils/fonts";
import { maskSecrets } from "./maskSecrets";

// NEW: site claims review required for both notices.
const NOTICES = {
  a2a: "A2A settings are read-only here. This editor does not send A2A settings when you save.",
  audit:
    "Audit settings are read-only here. This editor does not send audit settings when you save.",
};

// Display only: no draft, onChange callback, data hook or mutation. The editor
// separately excludes these sections from its save allowlist. Mask credentials
// using the same display boundary as Full config and configuration history.
export default function ReadOnlyOrgConfigTab({ section, config }) {
  const noticeId = useId();
  const label = section === "a2a" ? "A2A" : "audit";
  return (
    <Stack spacing={2} data-read-only="">
      <Typography typography="s2" color="text.secondary" id={noticeId}>
        {NOTICES[section]}
      </Typography>
      {isStored(config) ? (
        <TextField
          label={`Stored ${label} settings`}
          value={JSON.stringify(maskSecrets(config), null, 2)}
          multiline
          minRows={6}
          maxRows={16}
          fullWidth
          InputProps={{ readOnly: true }}
          inputProps={{ "aria-describedby": noticeId }}
          sx={{ "& .MuiInputBase-input": { fontFamily: GATEWAY_MONO } }}
        />
      ) : (
        // NEW: site claims review required for both empty-state strings.
        <Typography typography="s2" color="text.secondary">
          {`No ${label} settings are stored.`}
        </Typography>
      )}
    </Stack>
  );
}

ReadOnlyOrgConfigTab.propTypes = {
  section: PropTypes.oneOf(["a2a", "audit"]).isRequired,
  config: PropTypes.object,
};
