import PropTypes from "prop-types";
import React, { useCallback } from "react";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import { useGetPromptLabels } from "src/api/prompt/prompt-labels";
import LabelSelectContent from "./LabelSelectContent";

const LabelSelectPopover = ({
  open: ModalOpen,
  handleClose,
  version,
  promptId,
  selectedLabels = [],
  onSuccess = () => {},
  versionId,
}) => {
  const {
    data: labels = [],
    isPending,
    isFetchingNextPage,
    fetchNextPage,
  } = useGetPromptLabels();

  const handleCloseModal = useCallback(() => {
    handleClose?.();
  }, [handleClose]);

  return (
    <ModalWrapper
      open={ModalOpen}
      modalWidth="500px"
      onClose={handleCloseModal}
      title="Add Tags"
      subTitle="Save the prompt along with tags to track changes and support prompt reusability."
      actionBtnTitle="Save"
      hideCancelBtn={true}
      paperSx={{ gap: 1 }}
      dialogActionSx={{
        display: "none",
      }}
    >
      <LabelSelectContent
        promptId={promptId}
        versionId={versionId}
        selectedLabels={selectedLabels}
        labels={labels}
        onSuccess={onSuccess}
        onClose={handleCloseModal}
        version={version}
        isPending={isPending}
        isFetchingNextPage={isFetchingNextPage}
        fetchNextPage={fetchNextPage}
      />
    </ModalWrapper>
  );
};

LabelSelectPopover.propTypes = {
  open: PropTypes.bool.isRequired,
  handleClose: PropTypes.func.isRequired,
  promptId: PropTypes.string.isRequired,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.object]).isRequired,
  selectedLabels: PropTypes.array,
  onSuccess: PropTypes.func,
  versionId: PropTypes.string,
};

export default LabelSelectPopover;
