import { useMutation } from "@tanstack/react-query";
import React from "react";
import axios, { endpoints } from "src/utils/axios";
import { useDatasetOptimizationStoreShallow } from "./states";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import PropTypes from "prop-types";

const StopOptimizationModal = ({ onSuccess }) => {
  const { stopOptimizationId, setStopOptimizationId } =
    useDatasetOptimizationStoreShallow((state) => ({
      stopOptimizationId: state.stopOptimizationId,
      setStopOptimizationId: state.setStopOptimizationId,
    }));

  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      axios.post(endpoints.optimization.stopOptimization(stopOptimizationId)),
    onSuccess: () => {
      setStopOptimizationId(null);
      onSuccess?.();
    },
    // W6 C6: a run that finished before Stop answers 400 ("Cannot stop
    // optimization with status: completed", shown by the axios toast); the
    // dialog closes and the list re-reads, so the row shows its real status.
    onError: () => {
      setStopOptimizationId(null);
      onSuccess?.();
    },
  });

  return (
    <ModalWrapper
      open={!!stopOptimizationId}
      onClose={() => setStopOptimizationId(null)}
      title="Stop optimization run"
      subTitle="This will immediately stop the current optimization run. Any in-progress trials will be terminated. Do you want to proceed?"
      isValid={true}
      modalWidth="480px"
      onSubmit={() => mutate()}
      onCancelBtn={() => setStopOptimizationId(null)}
      actionBtnTitle="Stop Optimization"
      actionBtnSx={{
        bgcolor: "red.500",
        color: "common.white",
        "&:hover": { bgcolor: "red.600" },
      }}
      isLoading={isPending}
    />
  );
};

export default StopOptimizationModal;
StopOptimizationModal.propTypes = {
  onSuccess: PropTypes.func,
};
