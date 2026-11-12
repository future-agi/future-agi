// The per-user star on a dataset (W9-SCOPE §5 A4): in the list's Name cell
// (and the header after W4 A, integration H2). Starred datasets come first in
// the list and in every dataset picker (the server orders them). The
// state flips at once and returns if the server refuses, with its sentence.
import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { notify } from "src/sections/common/ux/toast";
import { errorSentence, useDatasetStar } from "src/api/develop/dataset-org";
import useDatasetStarSupport from "../hooks/useDatasetStarSupport";

const STAR_TIP = "Star · starred datasets come first in the list";
const UNSTAR_TIP = "Starred · click to unstar";

// A disabled button takes no pointer events, so the browser gives its click
// to the wrapper. Stopping it there keeps the dataset row from opening.
const stopRowClick = (event) => event.stopPropagation();

export default function DatasetStarToggle({ dataset, size = 16 }) {
  const [on, setOn] = useState(Boolean(dataset?.starred));
  const [saving, setSaving] = useState(false);
  useEffect(() => setOn(Boolean(dataset?.starred)), [dataset?.starred]);
  const star = useDatasetStar();
  const support = useDatasetStarSupport();
  const toggle = async (e) => {
    e.stopPropagation();
    if (saving || !support.available) return;
    setSaving(true);
    const next = !on;
    setOn(next);
    try {
      await star.mutateAsync({ id: dataset.id, on: next });
    } catch (err) {
      setOn(!next);
      notify(errorSentence(err), { variant: "error", key: "dataset-star" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <CustomTooltip
      show
      title={support.reason ?? (on ? UNSTAR_TIP : STAR_TIP)}
      size="small"
    >
      <span style={{ display: "inline-flex" }} onClick={stopRowClick}>
        <IconButton
          size="small"
          onClick={toggle}
          disabled={saving || !support.available}
          aria-pressed={on}
          aria-label={`Star ${dataset?.name ?? "dataset"}`}
          data-starred={on ? "true" : "false"}
          sx={{ p: 0.25, color: on ? "warning.main" : "text.disabled" }}
        >
          <Iconify icon={on ? "mdi:star" : "mdi:star-outline"} width={size} />
        </IconButton>
      </span>
    </CustomTooltip>
  );
}

DatasetStarToggle.propTypes = {
  dataset: PropTypes.shape({
    id: PropTypes.string.isRequired,
    name: PropTypes.string,
    starred: PropTypes.bool,
  }).isRequired,
  size: PropTypes.number,
};
