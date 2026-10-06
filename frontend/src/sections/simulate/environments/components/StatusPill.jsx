import PropTypes from "prop-types";
import { alpha, keyframes } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { STATUS_META, ENV_STATUS } from "../myEnvironments.constants";

const pulse = keyframes`
  0%,100% { opacity: 0.55; }
  50%     { opacity: 1; }
`;

export default function StatusPill({ status, progress }) {
  const meta = STATUS_META[status] || STATUS_META.not_run;
  const detail =
    status === ENV_STATUS.BUILDING && progress
      ? `${progress.done}/${progress.total} steps`
      : "";
  const isAnimated =
    status === ENV_STATUS.BUILDING ||
    status === ENV_STATUS.FINALIZING ||
    status === ENV_STATUS.CANCELLING ||
    status === ENV_STATUS.RUNNING;

  return (
    <CustomTooltip show={!!detail} arrow size="small" title={detail}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{
          display: "inline-flex",
          px: 0.875,
          py: 0.375,
          borderRadius: 999,
          border: "1px solid",
          borderColor: alpha(meta.color, 0.35),
          bgcolor: (t) =>
            alpha(meta.color, t.palette.mode === "dark" ? 0.14 : 0.09),
        }}
      >
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: meta.color,
            animation: isAnimated ? `${pulse} 1.4s ease-in-out infinite` : "none",
          }}
        />
        <Typography
          sx={{ typography: "s3", fontWeight: "fontWeightBold", color: meta.color }}
        >
          {meta.label}
        </Typography>
      </Stack>
    </CustomTooltip>
  );
}

StatusPill.propTypes = {
  status: PropTypes.string,
  progress: PropTypes.shape({
    done: PropTypes.number,
    total: PropTypes.number,
  }),
};
