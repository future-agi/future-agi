import PropTypes from "prop-types";
import { MenuItem, Select } from "@mui/material";
import { fDateTime } from "src/utils/format-time";
import { ATTEMPT_COPY as C } from "./attempts.constants";

/**
 * Which attempt of a re-run call the per-attempt tabs show: the call itself (latest) or an earlier one the re-run
 * kept. Numbers and dates are the server's; nothing shows when the call has no earlier attempt.
 */
export default function AttemptSelect({
  attemptNumber,
  earlierAttempts,
  value,
  onChange,
}) {
  if (!earlierAttempts?.length) return null;
  return (
    <Select
      size="small"
      value={value ?? ""}
      displayEmpty
      onChange={(event) => onChange(event.target.value || null)}
      SelectDisplayProps={{ "aria-label": C.label }}
      sx={{ typography: "s2", minWidth: 200 }}
    >
      <MenuItem value="">{C.latest(attemptNumber)}</MenuItem>
      {earlierAttempts.map((one) => (
        <MenuItem key={one.attempt} value={one.attempt}>
          {C.earlier(one.number, fDateTime(one.rerun_at))}
        </MenuItem>
      ))}
    </Select>
  );
}

AttemptSelect.propTypes = {
  attemptNumber: PropTypes.number,
  earlierAttempts: PropTypes.arrayOf(
    PropTypes.shape({
      attempt: PropTypes.string.isRequired,
      number: PropTypes.number.isRequired,
      rerun_at: PropTypes.string.isRequired,
    }),
  ),
  // An earlier attempt's id; null for the call itself.
  value: PropTypes.string,
  onChange: PropTypes.func.isRequired,
};
