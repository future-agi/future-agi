import PropTypes from "prop-types";
import { Box, ButtonBase, Typography } from "@mui/material";
import ComingSoonChip from "src/sections/simulate/environments/components/ComingSoonChip";
import LogoTile from "src/sections/simulate/environments/buildDrawer/LogoTile";

// One framework that can't be connected yet: greyed out, with its Soon chip.
function SoonRow({ item }) {
  return (
    <ButtonBase
      disabled
      sx={{
        width: "100%",
        justifyContent: "flex-start",
        textAlign: "left",
        gap: 1.25,
        px: 1,
        minHeight: 36,
        borderRadius: "6px",
        "&.Mui-disabled": { opacity: 0.6 },
      }}
    >
      <Box sx={{ display: "flex", filter: "grayscale(1)", opacity: 0.7 }}>
        <LogoTile logo={item.logo} icon={item.icon} muted />
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography
          noWrap
          sx={{ typography: "s1", fontWeight: 500, color: "text.secondary" }}
        >
          {item.name}
        </Typography>
      </Box>
      <ComingSoonChip compact />
    </ButtonBase>
  );
}
SoonRow.propTypes = { item: PropTypes.object.isRequired };

/** The SDK frameworks, three to a line, none of them connectable yet. */
export default function SoonFrameworks({ items }) {
  return (
    <Box
      sx={(t) => ({
        display: "grid",
        gridTemplateColumns: "1fr",
        p: 1,
        [t.breakpoints.up("md")]: {
          gridTemplateColumns: "repeat(3, 1fr)",
          columnGap: 4,
        },
      })}
    >
      {items.map((item) => (
        <SoonRow key={item.id} item={item} />
      ))}
    </Box>
  );
}
SoonFrameworks.propTypes = { items: PropTypes.array.isRequired };
