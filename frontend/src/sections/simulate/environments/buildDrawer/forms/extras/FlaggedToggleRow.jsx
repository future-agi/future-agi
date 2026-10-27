import PropTypes from "prop-types";
import { Box, Stack, Switch, Typography } from "@mui/material";
import UiOnlyFlag from "../../../components/UiOnlyFlag";
import { ROW_BOX_SX, T12 } from "../../drawerStyles";
import { HintLine } from "../formBits";

/**
 * A ToggleRow whose setting has no API yet: it works, carries a "UI only"
 * flag beside its title, and its value is never sent.
 */
export default function FlaggedToggleRow({
  title,
  body,
  hint,
  checked,
  onChange,
}) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1.5}
      sx={{ ...ROW_BOX_SX, px: 1.75, py: 1.25 }}
    >
      <Box flex={1} minWidth={0}>
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <Typography sx={{ ...T12, fontWeight: 600 }}>{title}</Typography>
          <UiOnlyFlag />
        </Stack>
        {body && (
          <Typography
            component="div"
            sx={{ ...T12, color: "text.secondary", mt: 0.25 }}
          >
            <HintLine text={body} hint={hint} />
          </Typography>
        )}
      </Box>
      <Switch
        size="small"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
        inputProps={{ "aria-label": title }}
      />
    </Stack>
  );
}
FlaggedToggleRow.propTypes = {
  title: PropTypes.string.isRequired,
  body: PropTypes.node,
  hint: PropTypes.string,
  checked: PropTypes.bool,
  onChange: PropTypes.func.isRequired,
};
