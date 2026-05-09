import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  IconButton,
  useTheme,
  Button,
  DialogActions,
} from "@mui/material";
import PropTypes from "prop-types";
import React, { useCallback, useRef, useState } from "react";
import PromptTopSection from "./PromptCardTopSection";
import PromptEditor from "./PromptEditor";
import { ShowComponent } from "../show";
import PromptRecorder from "./PromptRecorder";
import UploadMedia from "./UploadMedia/UploadMedia";
import ViewReplaceImage from "./ViewReplaceImage";
import { usePromptCardDefaultValues } from "./usePromptCardDefaultValues";
import {
  PromptContentTypes,
  PromptEditorPlaceholder,
  PromptRoles,
} from "../../utils/constants";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";
import {
  embedAudios,
  embedImages,
  embedPdfs,
  handleRemoveImage,
  handleReplaceImage,
  placeEditBolt,
} from "./common";
import { getRandomId } from "src/utils/utils";
import SvgColor from "../svg-color";

export default function ExpandedPrompt({
  open,
  onClose,
  label = "Prompt",
  role,
  viewOptions,
  index,
  onRoleChange,
  onGeneratePrompt,
  onImprovePrompt,
  isSync,
  required,
  onSyncChange,
  appliedVariableData,
  openVariableEditor,
  dropdownOptions,
  showEditEmbed,
  mentionEnabled,
  mainEditorRef,
  defaultValue = [],
  setSelectedImageMain,
  allowVariables,
  hideExpandedHeader,
  allVariablesValid = false,
  variableValidator,
  jinjaMode = false,
}) {
  const theme = useTheme();
  const quillRef = useRef(null);
  const { id } = useParams();
  const [prompt, setPrompt] = useState(defaultValue);
  const {
    allowRoleChange,
    allowImprovePrompt,
    allowGeneratePrompt,
    allowAttachment,
    allowSync,
  } = usePromptCardDefaultValues({ role, index, viewOptions, prompt });

  const [isRecorderActive, setIsRecorderActive] = useState(false);
  const [openUploadMedia, setOpenUploadMedia] = useState(null);
  const [selectedImage, setSelectedImage] = useState(null);

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

  const handlePromptChange = (content) => {
    setPrompt(content);
  };

  const cursorPosition = useRef(0);

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

  const handleRemoveImageMain = useCallback(
    (imageId) => {
      const quill = mainEditorRef.current;
      if (!quill) return;
      const delta = quill.getContents();
      let index = 0;
      let found = false;

      for (let i = 0; i < delta.ops.length; i++) {
        const op = delta.ops[i];
        if (op.insert?.ImageBlot && op.insert.ImageBlot.id === imageId) {
          found = true;
          break;
        } else if (typeof op.insert === "string") {
          index += op.insert.length;
        } else {
          index += 1;
        }
      }

      if (found) {
        quill.deleteText(index, 1, "api");
      }
    },
    [mainEditorRef],
  );

  const handleRemoveAudio = useCallback(
    (audioId) => {
      const quill = mainEditorRef.current;
      if (!quill) return;
      const delta = quill.getContents();
      let index = 0;
      let found = false;

      for (let i = 0; i < delta.ops.length; i++) {
        const op = delta.ops[i];
        if (op.insert?.AudioBlot && op.insert.AudioBlot.id === audioId) {
          found = true;
          break;
        } else if (typeof op.insert === "string") {
          index += op.insert.length;
        } else {
          index += 1;
        }
      }

      if (found) {
        quill.deleteText(index, 1, "api");
      }
    },
    [mainEditorRef],
  );

  const handleRemovePdf = useCallback(
    (pdfId) => {
      const quill = mainEditorRef.current;
      if (!quill) return;
      const delta = quill.getContents();
      let index = 0;
      let found = false;

      for (let i = 0; i < delta.ops.length; i++) {
        const op = delta.ops[i];
        if (op.insert?.PdfBlot && op.insert.PdfBlot.id === pdfId) {
          found = true;
          break;
        } else if (typeof op.insert === "string") {
          index += op.insert.length;
        } else {
          index += 1;
        }
      }

      if (found) {
        quill.deleteText(index, 1, "api");
      }
    },
    [mainEditorRef],
  );

  const setBlocksFromPrompt = () => {
    if (mainEditorRef?.current) {
      const blocks = prompt || [];
      const delta = { ops: [] };

      blocks.forEach((block) => {
        if (block.type === PromptContentTypes.IMAGE_URL) {
          // Add image embed
          delta.ops.push({
            insert: {
              ImageBlot: {
                url: block?.imageUrl?.url,
                name: block?.imageUrl?.imgName || block?.imageUrl?.img_name,
                size: block?.imageUrl?.imgSize || block?.imageUrl?.img_size,
                setSelectedImage: setSelectedImageMain,
                id: getRandomId(),
                handleRemoveImage: handleRemoveImageMain,
              },
            },
          });
          // Add newline after image unless it's the last block
          // if (index < blocks.length - 1) {
          //   delta.ops.push({ insert: "\n" });
          // }
        } else if (block.type === PromptContentTypes.AUDIO_URL) {
          delta.ops.push({
            insert: {
              AudioBlot: {
                url: block?.audioUrl?.url,
                name: block?.audioUrl?.audioName || block?.audioUrl?.audio_name,
                size: block?.audioUrl?.audioSize || block?.audioUrl?.audio_size,
                mimeType:
                  block?.audioUrl?.audioType || block?.audioUrl?.audio_type,
                id: getRandomId(),
                handleRemoveAudio,
              },
            },
          });
        } else if (block.type === PromptContentTypes.PDF_URL) {
          delta.ops.push({
            insert: {
              PdfBlot: {
                url: block?.pdfUrl?.url,
                name: block?.pdfUrl?.fileName || block?.pdfUrl?.file_name,
                size: block?.pdfUrl?.pdfSize || block?.pdfUrl?.pdf_size,
                id: getRandomId(),
                handleRemovePdf,
              },
            },
          });
        } else if (block.type === PromptContentTypes.TEXT) {
          // Add text content
          delta.ops.push({ insert: block.text });
          // Add newline after text unless it's the last block
          // if (index < blocks.length - 1) {
          //   delta.ops.push({ insert: "\n" });
          // }
        }
      });

      const lastBlock = blocks?.[blocks?.length - 1] ?? blocks?.[0];
      if (
        lastBlock?.type !== PromptContentTypes.TEXT ||
        lastBlock?.text === ""
      ) {
        delta.ops.push({ insert: "\n" });
      }
      mainEditorRef.current.setContents(delta);
      if (allowVariables) {
        placeEditBolt(
          mainEditorRef.current,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }
    }
  };

  const handleSave = () => {
    setBlocksFromPrompt();
    onClose();
  };

  const handleImprovePrompt = () => {
    setBlocksFromPrompt();
    onImprovePrompt();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      keepMounted={false}
      PaperProps={{
        sx: {
          minWidth: "700px",
          maxWidth: "700px",
          minHeight: "300px",
          overflow: "visible",
          padding: 2,
          display: "flex",
          flexDirection: "column",
          gap: 2,
        },
      }}
      sx={{
        zIndex: 1200,
      }}
    >
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          padding: 0,
        }}
      >
        {label}
        <IconButton
          sx={{
            padding: 0,
          }}
          onClick={onClose}
        >
          <SvgColor
            src="/assets/icons/ic_close.svg"
            sx={{
              height: theme.spacing(3),
              width: theme.spacing(3),
              color: "text.primary",
            }}
          />
        </IconButton>
      </DialogTitle>
      <DialogContent
        sx={{
          overflowY: "unset",
          paddingX: 0,
        }}
      >
        <Box
          sx={{
            borderRadius: 1,
            // height: "100%",
            gap: "16px",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <ShowComponent condition={!hideExpandedHeader}>
            <PromptTopSection
              role={role}
              onAttachment={allowAttachment ? onAttachment : undefined}
              onGeneratePrompt={
                allowGeneratePrompt ? onGeneratePrompt : undefined
              }
              onImprovePrompt={
                allowImprovePrompt ? handleImprovePrompt : undefined
              }
              onRoleChange={allowRoleChange ? onRoleChange : undefined}
              onSyncChange={allowSync ? onSyncChange : undefined}
              isSync={isSync}
              required={required}
              hideMenu={true}
            />
          </ShowComponent>
          <PromptEditor
            placeholder={PromptEditorPlaceholder[role]}
            appliedVariableData={appliedVariableData}
            prompt={prompt}
            onPromptChange={handlePromptChange}
            openVariableEditor={openVariableEditor}
            ref={quillRef}
            onSelectionChange={onSelectionChange}
            setSelectedImage={setSelectedImage}
            dropdownOptions={dropdownOptions}
            showEditEmbed={false}
            mentionEnabled={mentionEnabled}
            allowVariables={allowVariables}
            allVariablesValid={allVariablesValid}
            variableValidator={variableValidator}
            jinjaMode={jinjaMode}
            label={hideExpandedHeader ? role : ""}
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
          <DialogActions
            sx={{
              padding: 0,
            }}
          >
            <Button
              size="small"
              onClick={handleSave}
              sx={{
                width: "167px",
              }}
              color="primary"
              variant="contained"
            >
              Save
            </Button>
          </DialogActions>
        </Box>
      </DialogContent>
    </Dialog>
  );
}

ExpandedPrompt.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  label: PropTypes.string,
  role: PropTypes.oneOf(Object.values(PromptRoles)).isRequired,
  viewOptions: PropTypes.shape({
    allowRoleChange: PropTypes.bool,
    allowRemove: PropTypes.bool,
    allowImprovePrompt: PropTypes.bool,
    allowGeneratePrompt: PropTypes.bool,
    allowAttachment: PropTypes.bool,
    allowSync: PropTypes.bool,
  }),
  index: PropTypes.number.isRequired,
  onRoleChange: PropTypes.func,
  onGeneratePrompt: PropTypes.func,
  onImprovePrompt: PropTypes.func,
  isSync: PropTypes.bool,
  onSyncChange: PropTypes.func,
  required: PropTypes.bool,
  appliedVariableData: PropTypes.object,
  openVariableEditor: PropTypes.func,
  dropdownOptions: PropTypes.array,
  showEditEmbed: PropTypes.bool,
  mentionEnabled: PropTypes.bool,
  mainEditorRef: PropTypes.object,
  defaultValue: PropTypes.array,
  setSelectedImageMain: PropTypes.func,
  allowVariables: PropTypes.bool,
  hideExpandedHeader: PropTypes.bool,
  allVariablesValid: PropTypes.bool,
  variableValidator: PropTypes.func,
  jinjaMode: PropTypes.bool,
};
