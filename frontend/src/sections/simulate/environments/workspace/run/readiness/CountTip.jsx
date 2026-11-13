import { useState } from "react";
import PropTypes from "prop-types";
import { Box } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import {
  MAX_CASE_KEYS,
  useReadinessCases,
} from "src/api/simulate-environments/readiness";
import { READY_TO_RUN_COPY as COPY } from "./readiness.constants";

const MAX_NAMES = 10;

// A count that names its scenarios on hover: the first page the server
// lists, and how many more from the count itself.
export default function CountTip({
  envId,
  picks,
  keys,
  bucket,
  count,
  report,
  children,
}) {
  const [shown, setShown] = useState(false);
  const tooMany = (keys?.length || 0) > MAX_CASE_KEYS;
  const cases = useReadinessCases(envId, {
    picks,
    bucket,
    keys,
    report,
    enabled: shown && !tooMany,
  });
  const names = (cases.data || [])
    .slice(0, MAX_NAMES)
    .map((one) => ({ key: one.key, name: one.name || one.key }));
  let title = COPY.namesLoading;
  if (tooMany) title = COPY.namesTooMany;
  else if (cases.isError) title = COPY.namesError;
  else if (cases.data) {
    title = (
      <Box component="ul" sx={{ m: 0, pl: 2 }}>
        {names.map((one) => (
          <Box component="li" key={one.key}>
            {one.name}
          </Box>
        ))}
        {count > names.length && (
          <Box component="li" sx={{ listStyle: "none", ml: -2 }}>
            {COPY.namesMore(count - names.length)}
          </Box>
        )}
      </Box>
    );
  }
  return (
    <CustomTooltip
      show
      title={title}
      size="small"
      arrow
      onOpen={() => setShown(true)}
    >
      <Box
        component="span"
        tabIndex={0}
        sx={{ textDecoration: "underline dotted", cursor: "default" }}
      >
        {children}
      </Box>
    </CustomTooltip>
  );
}

CountTip.propTypes = {
  envId: PropTypes.string,
  picks: PropTypes.object,
  keys: PropTypes.arrayOf(PropTypes.string),
  bucket: PropTypes.string.isRequired,
  count: PropTypes.number,
  report: PropTypes.string,
  children: PropTypes.node,
};
