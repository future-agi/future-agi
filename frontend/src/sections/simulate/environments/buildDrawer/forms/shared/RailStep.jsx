import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { T12, T13 } from "../../drawerStyles";

/** One numbered step of a setup, on a thin rail down to the next. */
export default function RailStep({ n, title, body, last = false, children }) {
  return (
    <Stack direction="row" spacing={1.75}>
      <Stack alignItems="center" sx={{ flexShrink: 0 }}>
        <Box
          sx={{
            width: 22,
            height: 22,
            borderRadius: "50%",
            display: "grid",
            placeItems: "center",
            border: "1px solid",
            borderColor: "divider",
            ...T12,
            fontWeight: 600,
            color: "text.secondary",
          }}
        >
          {n}
        </Box>
        {!last && (
          <Box
            sx={{
              flex: 1,
              width: "1px",
              bgcolor: "divider",
              my: 0.75,
              minHeight: 20,
            }}
          />
        )}
      </Stack>
      <Box sx={{ flex: 1, minWidth: 0, pb: last ? 0 : 2 }}>
        <Typography sx={{ ...T13, fontWeight: 600, lineHeight: "22px" }}>
          {title}
        </Typography>
        <Typography
          component="div"
          sx={{ ...T12, color: "text.secondary", mb: 1 }}
        >
          {body}
        </Typography>
        {children}
      </Box>
    </Stack>
  );
}
RailStep.propTypes = {
  n: PropTypes.number,
  title: PropTypes.node,
  body: PropTypes.node,
  last: PropTypes.bool,
  children: PropTypes.node,
};
