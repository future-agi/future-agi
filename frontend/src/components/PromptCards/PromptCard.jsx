import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  PromptRoles,
  PromptContentTypes,
  PromptEditorPlaceholder,
} from "src/utils/constants";
import { PromptCardWrapper } from "./PromptCardStyleComponents";
import PromptTopSection from "./PromptCardTopSection";
import PromptEditor from "./PromptEditor";
import { usePromptCardDefaultValues } from "./usePromptCardDefaultValues";
import UploadMedia from "./UploadMedia/UploadMedia";
import {
  embedAudios,
  embedImages,
  embedPdfs,
  handleRemoveImage,
  handleReplaceImage,
} from "./common";
import ViewReplaceImage from "./ViewReplaceImage";
import PromptRecorder from "./PromptRecorder";
import { ShowComponent } from "../show";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
// import { IconButton } from "@mui/material";
// import Iconify from "../iconify";
import ExpandedPrompt from "./ExpandedPrompt";

const PromptCard = ({
  prompt,
  role,
  onPromptChange,
  onRoleChange,
  viewOptions,
  index,
  onRemove,
  appliedVariableData,
  openVariableEditor,
  dropdownOptions = [],
  showEditEmbed,
  mentionEnabled,
  onGeneratePrompt,
  onImprovePrompt,
  isSync,
  onSyncChange,
  inputRef,
  required,
  disabled,
  expandable,
  expandPrompt,
  setExpandPrompt,
  existingRoles = [],
  onCopyPrompt,
  dragHandleProps,
  hideExpandedHeader,
  allVariablesValid = false,
  variableValidator,
  jinjaMode = false,
}) => {
  const {
    allowRoleChange,
    allowRemove,
    allowImprovePrompt,
    allowGeneratePrompt,
    allowAttachment,
    allowSync,
  } = usePromptCardDefaultValues({ role, index, viewOptions, prompt });
  const { id } = useParams();

  const quillRef = useRef(null);

  const cursorPosition = useRef(0);

  const [selectedImage, setSelectedImage] = useState(null);

  const [openUploadMedia, setOpenUploadMedia] = useState(null);

  const [isRecorderActive, setIsRecorderActive] = useState(false);

  const onAttachment = (type) => {
    if (type === "recordAudio") {
      setIsRecorderActive(true);
      return;
    }
    setOpenUploadMedia(type);
    if (window.location.pathname.includes("dashboard/workbench/create")) {
      trackEvent(Events.promptAttachmediaClicked, {
        [PropertyName.promptId]: id,
      });
    }
  };

  // const onImprovePrompt = () => {};

  const handleEmbedMedia = (type, addedMedia) => {
    const quill = quillRef.current;
    if (type === "image") {
      embedImages(
        addedMedia,
        quill,
        setSelectedImage,
        quill?.getSelection(true)?.index,
      );
    } else if (type === "audio") {
      embedAudios(addedMedia, quill, quill?.getSelection(true)?.index);
    } else if (type === "pdf") {
      embedPdfs(addedMedia, quill, quill?.getSelection(true)?.index);
    }
  };

  const onSelectionChange = (range) => {
    if (!range) return;
    cursorPosition.current = range.index;
  };

  return (
    <>
      <PromptCardWrapper>
        <PromptTopSection
          role={role}
          onDelete={allowRemove ? onRemove : undefined}
          onAttachment={allowAttachment ? onAttachment : undefined}
          onGeneratePrompt={allowGeneratePrompt ? onGeneratePrompt : undefined}
          onImprovePrompt={allowImprovePrompt ? onImprovePrompt : undefined}
          onRoleChange={allowRoleChange ? onRoleChange : undefined}
          onSyncChange={allowSync ? onSyncChange : undefined}
          isSync={isSync}
          required={required}
          disabled={disabled}
          existingRoles={existingRoles}
          allowAllRoleChange={viewOptions?.allowAllRoleChange}
          compact={viewOptions?.compact}
          onCopyPrompt={onCopyPrompt}
          dragHandleProps={dragHandleProps}
          sortable={viewOptions?.sortable}
          expandable={expandable}
          onExpandPrompt={() => setExpandPrompt(true)}
        />
        <PromptEditor
          placeholder={PromptEditorPlaceholder[role]}
          appliedVariableData={appliedVariableData}
          prompt={prompt}
          onPromptChange={onPromptChange}
          openVariableEditor={openVariableEditor}
          ref={quillRef}
          onSelectionChange={onSelectionChange}
          setSelectedImage={setSelectedImage}
          dropdownOptions={dropdownOptions}
          showEditEmbed={showEditEmbed}
          mentionEnabled={mentionEnabled}
          allowVariables
          inputRef={inputRef}
          expandable={expandable}
          disabled={disabled}
          allVariablesValid={allVariablesValid}
          variableValidator={variableValidator}
          jinjaMode={jinjaMode}
        />
        <ShowComponent condition={isRecorderActive}>
          <PromptRecorder
            onClose={() => setIsRecorderActive(false)}
            handleEmbedMedia={handleEmbedMedia}
          />
        </ShowComponent>
        <UploadMedia
          open={Boolean(openUploadMedia)}
          onClose={() => setOpenUploadMedia(null)}
          type={openUploadMedia}
          handleEmbedMedia={handleEmbedMedia}
        />
        <ViewReplaceImage
          open={Boolean(selectedImage)}
          onClose={() => setSelectedImage(null)}
          selectedImage={selectedImage}
          onImageDelete={handleRemoveImage(quillRef.current)}
          onImageReplace={(imgId, newImageData) =>
            handleReplaceImage(quillRef.current)(
              imgId,
              newImageData,
              setSelectedImage,
            )
          }
        />
      </PromptCardWrapper>
      {expandPrompt && (
        <ExpandedPrompt
          open={expandPrompt}
          onClose={() => setExpandPrompt(false)}
          index={index}
          role={role}
          viewOptions={viewOptions}
          onRoleChange={onRoleChange}
          onGeneratePrompt={onGeneratePrompt}
          onImprovePrompt={onImprovePrompt}
          isSync={isSync}
          required={required}
          showEditEmbed={showEditEmbed}
          mainEditorRef={quillRef}
          defaultValue={prompt}
          dropdownOptions={dropdownOptions}
          appliedVariableData={appliedVariableData}
          mentionEnabled={mentionEnabled}
          setSelectedImageMain={setSelectedImage}
          allowVariables
          hideExpandedHeader={hideExpandedHeader}
          openVariableEditor={openVariableEditor}
          allVariablesValid={allVariablesValid}
          variableValidator={variableValidator}
          jinjaMode={jinjaMode}
        />
      )}
    </>
  );
};

PromptCard.propTypes = {
  prompt: PropTypes.arrayOf(
    PropTypes.shape({
      type: PropTypes.oneOf(Object.values(PromptContentTypes)),
      content: PropTypes.oneOfType([
        PropTypes.string,
        PropTypes.shape({
          audioUrl: PropTypes.string.isRequired,
        }),
        PropTypes.shape({
          imageUrl: PropTypes.string.isRequired,
        }),
      ]),
    }),
  ).isRequired,
  onPromptChange: PropTypes.func.isRequired,
  onRoleChange: PropTypes.func.isRequired,
  role: PropTypes.oneOf(Object.values(PromptRoles)).isRequired,
  viewOptions: PropTypes.shape({
    allowRoleChange: PropTypes.bool,
    allowRemove: PropTypes.bool,
    allowImprovePrompt: PropTypes.bool,
    allowGeneratePrompt: PropTypes.bool,
    allowAttachment: PropTypes.bool,
    allowSync: PropTypes.bool,
    allowAllRoleChange: PropTypes.bool,
    sortable: PropTypes.bool,
    compact: PropTypes.bool,
  }),
  index: PropTypes.number.isRequired,
  onRemove: PropTypes.func,
  appliedVariableData: PropTypes.object,
  openVariableEditor: PropTypes.func,
  dropdownOptions: PropTypes.array,
  showEditEmbed: PropTypes.bool,
  mentionEnabled: PropTypes.bool,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  isSync: PropTypes.bool,
  onSyncChange: PropTypes.func,
  inputRef: PropTypes.object,
  required: PropTypes.bool,
  disabled: PropTypes.bool,
  expandable: PropTypes.bool,
  expandPrompt: PropTypes.bool,
  setExpandPrompt: PropTypes.func,
  existingRoles: PropTypes.arrayOf(PropTypes.string),
  onCopyPrompt: PropTypes.func,
  dragHandleProps: PropTypes.object,
  hideExpandedHeader: PropTypes.bool,
  allVariablesValid: PropTypes.bool,
  variableValidator: PropTypes.func,
  jinjaMode: PropTypes.bool,
};

export default PromptCard;
