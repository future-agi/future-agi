import PropTypes from "prop-types";
import { MenuItem, Select, Stack, Typography } from "@mui/material";
import { ALK_MONO } from "./alkTokens";

const SessionPicker = ({ sessions, openSessionId, busy, onOpen }) => {
  const open = sessions.find((one) => one.id === openSessionId) || null;

  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      {sessions.length === 0 ? (
        <Typography variant="body2" sx={{ fontFamily: ALK_MONO }}>
          no session — start one
        </Typography>
      ) : (
        <Select
          size="small"
          value={openSessionId || ""}
          disabled={busy}
          onChange={(event) => onOpen(event.target.value)}
          sx={{
            fontFamily: ALK_MONO,
            minWidth: 220,
            height: 36,
            fontSize: 13,
            "& .MuiSelect-select": {
              paddingTop: 0,
              paddingBottom: 0,
              lineHeight: "36px",
            },
          }}
          renderValue={() => open?.agent || open?.id || ""}
        >
          {sessions.map((one) => (
            <MenuItem key={one.id} value={one.id} sx={{ fontFamily: ALK_MONO }}>
              {one.agent || one.id}
            </MenuItem>
          ))}
        </Select>
      )}
    </Stack>
  );
};

SessionPicker.propTypes = {
  sessions: PropTypes.array.isRequired,
  openSessionId: PropTypes.string,
  busy: PropTypes.bool,
  onOpen: PropTypes.func.isRequired,
};

export default SessionPicker;
