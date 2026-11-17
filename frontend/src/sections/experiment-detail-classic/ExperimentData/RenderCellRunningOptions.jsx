import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  FormControlLabel,
  Typography,
  FormControl,
  RadioGroup,
  Radio,
} from "@mui/material";
import { useMutation } from "@tanstack/react-query";

import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";

const RERUN_OPTIONS = {
  ALL: "all",
  FAILED_ONLY: "failed_only",
};

const RenderCellRunningOptions = ({ sourceId, onClose, open, gridApiRef }) => {
  const [rerunType, setRerunType] = useState(RERUN_OPTIONS.ALL);
  const { experimentId } = useParams();
  const { mutate: handleRerun, isPending } = useMutation({
    mutationFn: () =>
      axios.post(
        endpoints.develop.experiment.reRunExperimentCell(experimentId),
        {
          source_ids: [sourceId],
          failed_only: rerunType === RERUN_OPTIONS.FAILED_ONLY,
        },
      ),

    onSuccess: () => {
      onClose?.();
      gridApiRef?.current?.refreshServerSide({ purge: false });
    },
  });

  const handleSubmit = () => {
    handleRerun();
  };

  return (
    <ModalWrapper
      open={open}
      onClose={onClose}
      title={"Rerun Options"}
      actionBtnTitle="Re-run"
      onSubmit={handleSubmit}
      isLoading={isPending}
      isValid={!!sourceId}
    >
      {/* Rerun Type Radio Group */}
      <FormControl>
        <RadioGroup
          name="rerun-options"
          value={rerunType}
          onChange={(e) => setRerunType(e.target.value)}
        >
          <FormControlLabel
            value={RERUN_OPTIONS.ALL}
            control={<Radio />}
            label={
              <Typography variant="s1" fontWeight="fontWeightMedium">
                Run all cells in the column
              </Typography>
            }
          />
          <FormControlLabel
            value={RERUN_OPTIONS.FAILED_ONLY}
            control={<Radio />}
            label={
              <Typography variant="s1" fontWeight="fontWeightMedium">
                Run only failed cells in the column
              </Typography>
            }
          />
        </RadioGroup>
      </FormControl>
    </ModalWrapper>
  );
};

RenderCellRunningOptions.propTypes = {
  sourceId: PropTypes.string,
  onClose: PropTypes.func,
  open: PropTypes.bool,
  gridApiRef: PropTypes.object,
};

export default RenderCellRunningOptions;
