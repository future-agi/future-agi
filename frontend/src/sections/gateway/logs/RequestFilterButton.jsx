import PropTypes from "prop-types";
import React, { useCallback, useMemo } from "react";
import { FilterButton } from "src/components/filter";
import { useAvailableModels } from "../hooks/useAvailableModels";
import useMetadataValues from "./hooks/useMetadataValues";
import {
  REQUEST_FILTER_KEYS,
  fromPanelValue,
  requestFilterFields,
  toPanelValue,
} from "./filterPanelAdapter";

/**
 * Request log filter: the shared Filter button and panel over the URL-synced
 * filter object. Keys the panel does not own (view, search, sort, gateway ...)
 * are kept; the page number restarts when filters change.
 */
const RequestFilterButton = ({ filters, onApply }) => {
  const availableModels = useAvailableModels();
  const { data: metadataValues } = useMetadataValues();
  const fields = useMemo(
    () => requestFilterFields(availableModels, metadataValues),
    [availableModels, metadataValues],
  );
  const value = useMemo(() => toPanelValue(filters), [filters]);

  const handleChange = useCallback(
    (rows) => {
      const kept = Object.fromEntries(
        Object.entries(filters).filter(
          ([key]) => key !== "page" && !REQUEST_FILTER_KEYS.includes(key),
        ),
      );
      onApply({ ...kept, ...fromPanelValue(rows) });
    },
    [filters, onApply],
  );

  return (
    <FilterButton
      fields={fields}
      value={value}
      onChange={handleChange}
      freeSoloValues
    />
  );
};

RequestFilterButton.propTypes = {
  filters: PropTypes.object.isRequired,
  onApply: PropTypes.func.isRequired,
};

export default RequestFilterButton;
