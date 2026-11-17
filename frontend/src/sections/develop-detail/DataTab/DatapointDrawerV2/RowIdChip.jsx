import React from "react";
import PropTypes from "prop-types";
import CopyId from "src/sections/common/ux/CopyId";
import { rowApiCopyProps } from "./rowApiPath";

/**
 * The Row view's id chip: copy id and link always; `Copy API path` and
 * `Copy as API call` only where rowApiCopyProps offers them.
 */
export default function RowIdChip({ datasetId, rowId, link }) {
  return (
    <CopyId
      id={rowId}
      short={12}
      link={link}
      {...rowApiCopyProps(datasetId, rowId)}
    />
  );
}

RowIdChip.propTypes = {
  datasetId: PropTypes.string,
  rowId: PropTypes.string,
  link: PropTypes.string,
};
