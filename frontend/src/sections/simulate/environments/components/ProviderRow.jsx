import PropTypes from "prop-types";
import { Stack } from "@mui/material";
import ChipCard from "./ChipCard";

export default function ProviderRow({ options, value, onChange }) {
  return (
    <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
      {(options ?? []).map((o) => (
        <ChipCard
          key={o.id}
          icon={o.icon}
          label={o.name}
          on={value === o.id}
          comingSoon={o.comingSoon}
          onClick={() => onChange?.(o.id)}
        />
      ))}
    </Stack>
  );
}
ProviderRow.propTypes = { options: PropTypes.array, value: PropTypes.string, onChange: PropTypes.func };
