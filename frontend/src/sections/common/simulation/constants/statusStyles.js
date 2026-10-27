import { palette } from "src/theme/palette";
import { alpha } from "@mui/material";

/**
 * Shared status styles for execution status cell renderers
 */
export const statusStyles = {
  NotStarted: {
    backgroundColor: palette("light").black["o5"],
    color: palette("light").black["800"],
  },
  Pending: {
    backgroundColor: palette("light").blue["o10"],
    color: palette("light").blue["500"],
  },
  Running: {
    backgroundColor: (theme) => alpha(theme.palette.primary.main, 0.1),
    color: "primary.main",
  },
  Completed: {
    backgroundColor: palette("light").green["o10"],
    color: palette("light").green["500"],
  },
  Evaluating: {
    backgroundColor: palette("light").blue["o10"],
    color: palette("light").blue["500"],
  },
  Editing: {
    backgroundColor: palette("light").blue["o10"],
    color: palette("light").blue["500"],
  },
  Inactive: {
    backgroundColor: palette("light").orange["50"],
    color: palette("light").orange["400"],
  },
  Failed: {
    backgroundColor: palette("light").red["o10"],
    color: palette("light").red["400"],
  },
  Cancelling: {
    backgroundColor: palette("light").red["o10"],
    color: palette("light").red["400"],
  },
  Cancelled: {
    backgroundColor: palette("light").red["o10"],
    color: palette("light").red["400"],
  },
};

export const STOPPABLE_STATUSES = ["Evaluating", "Running", "Pending"];

export const TERMINAL_STATUSES = ["Completed", "Failed", "Cancelled"];

export default statusStyles;
