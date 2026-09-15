import PropTypes from "prop-types";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import ComingSoonChip from "../components/ComingSoonChip";
import { activateOnKey } from "../helpers/activateOnKey";
import { OPTION_STATUS } from "../environmentOptions";

export default function OptionCard({ option, selected, onClick }) {
  const comingSoon = option?.status === OPTION_STATUS.COMING_SOON;
  const card = (
    <Stack
      onClick={comingSoon ? undefined : (e) => onClick?.(e)}
      role="button"
      tabIndex={comingSoon ? -1 : 0}
      onKeyDown={comingSoon ? undefined : activateOnKey((e) => onClick?.(e))}
      aria-pressed={selected}
      aria-disabled={comingSoon || undefined}
      spacing={1.5}
      sx={{
        position: "relative",
        p: 2,
        borderRadius: 1.5,
        cursor: comingSoon ? "default" : "pointer",
        opacity: comingSoon ? 0.72 : 1,
        border: "1px solid",
        minHeight: 180,
        borderColor: (th) => selected
          ? (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.5) : th.palette.text.primary)
          : th.palette.divider,
        bgcolor: (th) => selected
          ? alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.03)
          : "background.paper",
        transition: "border-color .12s ease, background-color .12s ease",
        "&:hover": {
          borderColor: (th) => (comingSoon || selected) ? undefined : (th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.32) : th.palette.text.disabled),
        },
      }}
    >
      <Stack direction="row" alignItems="center" spacing={1.25}>
        <Box
          sx={{
            width: 32, height: 32, borderRadius: 1,
            display: "grid", placeItems: "center", flexShrink: 0,
            bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.1 : 0.06),
            color: "text.primary",
          }}
        >
          <Iconify icon={option?.icon} width={16} />
        </Box>
        <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", lineHeight: 1.2 }}>
          {option?.title}
        </Typography>
      </Stack>
      <Typography sx={{ typography: "s3", color: "text.subtitle", lineHeight: 1.45, flex: 1 }}>
        {option?.blurb}
      </Typography>
      {option?.preview && (
        <Box
          sx={{
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap">
            {(option.preview ?? []).map((item) => (
              <Typography
                key={item}
                sx={{
                  typography: "s3",
                  fontWeight: "fontWeightSemiBold",
                  color: "text.subtitle",
                  px: 0.75,
                  py: 0.25,
                  borderRadius: 0.5,
                  bgcolor: (th) => alpha(th.palette.text.primary, th.palette.mode === "dark" ? 0.06 : 0.04),
                }}
              >
                {item}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}
      {selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={15}
          sx={{ position: "absolute", top: 10, right: 10, color: "text.primary" }}
        />
      )}
    </Stack>
  );
  if (comingSoon) {
    return (
      <Box sx={{ position: "relative", display: "grid" }}>
        {card}
        <ComingSoonChip sx={{ position: "absolute", top: 12, right: 12, zIndex: 1 }} />
      </Box>
    );
  }
  return card;
}
OptionCard.propTypes = {
  option: PropTypes.shape({
    id: PropTypes.string,
    title: PropTypes.node,
    icon: PropTypes.string,
    blurb: PropTypes.node,
    preview: PropTypes.arrayOf(PropTypes.string),
    status: PropTypes.string,
    setupSubtitle: PropTypes.string,
  }),
  selected: PropTypes.bool,
  onClick: PropTypes.func,
};
