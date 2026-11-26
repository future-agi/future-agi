import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import SideDrawer from "../../components/SideDrawer";
import { REVIEW_COPY as COPY } from "../workspace.constants";

export default function ReviewChangesFrame({
  open,
  onClose,
  titleRef,
  badge,
  subtitle,
  children,
}) {
  return (
    <SideDrawer open={open} onClose={onClose} width={640}>
      <Stack
        role="dialog"
        aria-label={COPY.title}
        sx={{ height: "100%", minHeight: 0 }}
      >
        <Box
          sx={{
            px: 2,
            py: 1.5,
            pr: 6,
            borderBottom: "1px solid",
            borderColor: "divider",
            flexShrink: 0,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              ref={titleRef}
              tabIndex={-1}
              component="h2"
              sx={{
                typography: "m1",
                fontWeight: "fontWeightSemiBold",
                outline: "none",
              }}
            >
              {COPY.title}
            </Typography>
            {badge}
          </Stack>
          {subtitle && (
            <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        {children}
      </Stack>
    </SideDrawer>
  );
}

ReviewChangesFrame.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  titleRef: PropTypes.object,
  badge: PropTypes.node,
  subtitle: PropTypes.node,
  children: PropTypes.node,
};
