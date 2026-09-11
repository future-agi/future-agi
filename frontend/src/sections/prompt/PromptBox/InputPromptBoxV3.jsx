import {
  Box,
  Button,
  debounce,
  Drawer,
  IconButton,
  useTheme,
} from "@mui/material";
import PropTypes from "prop-types";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { FormSelectField } from "src/components/FormSelectField";
import Iconify from "src/components/iconify";
import { ShowComponent } from "src/components/show";
import CustomTooltip from "src/components/tooltip";
import { Events, trackEvent } from "src/utils/Mixpanel";

import "./InputPromptV2.css";
import Quill from "quill";
import { htmlToText } from "html-to-text";
import { useController, useWatch } from "react-hook-form";
import { getRandomId } from "src/utils/utils";
import { ConfirmDialog } from "src/components/custom-dialog";

import GeneratePrompt from "../PromptDrawer/GeneratePrompt";

import UploadedImageViewer from "./UploadedImageViewer";
import GeneratePromptBolt from "./GeneratePromptBolt";
import CustomImageBlot from "./CustomImageBlot";

const Delta = Quill.import("delta");

Quill.register("formats/customImage", CustomImageBlot);
Quill.register("formats/generatePrompt", GeneratePromptBolt);

function parseHtmlToBlocks(htmlString) {
  // Use DOMParser instead of creating element in document
  const parser = new DOMParser();
  const doc = parser.parseFromString(htmlString, "text/html");
  const nodes = Array.from(doc.body.childNodes);

  const blocks = [];
  let currentTextBlock = [];

  nodes.forEach((node) => {
    if (node.nodeType === Node.ELEMENT_NODE) {
      if (node.classList.contains("fi-image-editor-container")) {
        // If we have accumulated text, push it as a text block
        if (currentTextBlock.length > 0) {
          blocks.push({
            type: "text",
            text: htmlToText(currentTextBlock.join(""), {
              wordwrap: false,
              selectors: [
                {
                  selector: "p",
                  options: { leadingLineBreaks: 1, trailingLineBreaks: 1 },
                },
                { selector: "br", options: { leadingLineBreaks: 1 } },
              ],
            }),
          });
          currentTextBlock = [];
        }

        // Find the image URL
        const imgElement = node.querySelector("img");
        if (imgElement && imgElement.src) {
          blocks.push({
            type: "image_url",
            imageUrl: {
              url: imgElement.src,
            },
          });
        }
      } else if (
        node?.getAttribute("data-type")?.startsWith("generatePrompt")
      ) {
        return;
      } else {
        // For non-image elements, add to text block
        currentTextBlock.push(node.outerHTML);
      }
    }
  });

  // Push any remaining text
  if (currentTextBlock.length > 0) {
    blocks.push({
      type: "text",
      text: htmlToText(currentTextBlock.join(""), {
        wordwrap: false,
        selectors: [
          {
            selector: "p",
            options: { leadingLineBreaks: 1, trailingLineBreaks: 1 },
          },
          { selector: "br", options: { leadingLineBreaks: 1 } },
        ],
      }),
    });
  }

  return blocks;
}

const InputPromptBoxV3 = ({
  viewOptions,
  onRemove,
  onDuplicate,
  control,
  roleFieldName,
  contentFieldName,
  appliedVariableData,
  handleLabelsAdd,
  index,
}) => {
  const selectRoleVisible = viewOptions?.selectRoleVisible ?? true;
  const quillRef = useRef(null);
  const [generatePromptOpen, setGeneratePromptOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const generatePromptId = useRef(null);
  const [isDirty, setIsDirty] = useState(false);
  const [openImprovePrompt, setOpenImprovePrompt] = useState(false);

  const handleClose = () => {
    if (isDirty) {
      setConfirmOpen(true);
    } else {
      if (openImprovePrompt) {
        setOpenImprovePrompt(false);
      } else {
        setGeneratePromptOpen(false);
      }
    }
  };

  const handleSetIsDirty = (dirty) => {
    setIsDirty(dirty);
  };

  const handleConfirmClose = () => {
    setConfirmOpen(false);
    if (openImprovePrompt) {
      setOpenImprovePrompt(false);
    } else {
      setGeneratePromptOpen(false);
    }
    setIsDirty(false);
  };

  const [selectedImageId, setSelectedImageId] = useState(null);

  const currentRole = useWatch({
    control,
    name: roleFieldName,
  });

  const currentContent = useWatch({
    control,
    name: contentFieldName,
  });

  const { field: contentField } = useController({
    control,
    name: contentFieldName,
  });

  const theme = useTheme();

  const setGeneratePromptVisibility = (quill, newRole = currentRole) => {
    const generatePromptElement = document.querySelector(
      `[data-type='generatePrompt-${generatePromptId.current}']`,
    );

    if (generatePromptElement) {
      //hide generate prompt element if getActualLength(quill) is > 0
      const actualLength = getActualLength(quill);

      if (actualLength > 0 || newRole !== "user") {
        generatePromptElement.style.display = "none";
      } else {
        generatePromptElement.style.display = "block";
      }
    }
  };

  const removeImageBlot = (imageId) => {
    const quill = quillRef.current;
    if (!quill) {
      return;
    }

    const delta = quill.getContents();

    let index = 0;
    let found = false;

    for (let i = 0; i < delta.ops.length; i++) {
      const op = delta.ops[i];
      if (op.insert?.customImage && op.insert.customImage.id === imageId) {
        found = true;
        break;
      }
      index += typeof op.insert === "string" ? op.insert.length : 1;
    }

    if (found) {
      quill.deleteText(index, 1);
    }
  };

  const replaceImageBlot = (imageId, newImageData) => {
    const quill = quillRef.current;
    if (!quill) {
      return;
    }

    // Find the index of the image to replace
    const delta = quill.getContents();
    let index = 0;
    let found = false;

    for (let i = 0; i < delta.ops.length; i++) {
      const op = delta.ops[i];
      if (op.insert?.customImage && op.insert.customImage.id === imageId) {
        found = true;
        break;
      }
      index += typeof op.insert === "string" ? op.insert.length : 1;
    }

    if (!found) {
      return;
    }

    removeImageBlot(imageId);

    // Insert the new image at the same position
    quill.insertEmbed(index, "customImage", {
      id: getRandomId(),
      src: newImageData.src,
      alt: newImageData.alt,
      setSelectedImageId: setSelectedImageId,
      removeImageBlot: removeImageBlot,
    });
    setGeneratePromptVisibility(quill);
  };

  const containerRef = useRef(null);
  const defaultValue = useMemo(() => {
    const blocks = contentField.value || [];
    const delta = { ops: [] };

    blocks.forEach((block, index) => {
      if (block.type === "image_url") {
        // Add image embed
        delta.ops.push({
          insert: {
            customImage: {
              src: block?.image_url?.url,
              alt: "Uploaded image",
              setSelectedImageId: setSelectedImageId,
              removeImageBlot: removeImageBlot,
              id: getRandomId(),
            },
          },
        });
        // Add newline after image unless it's the last block
        if (index < blocks.length - 1) {
          delta.ops.push({ insert: "\n" });
        }
      } else if (block.type === "text") {
        // Add text content
        delta.ops.push({ insert: block.text });
        // Add newline after text unless it's the last block
        if (index < blocks.length - 1) {
          delta.ops.push({ insert: "\n" });
        }
      }
    });

    return delta;
  }, [contentField.value]);

  const applyFormatting = (quill) => {
    if (!quill) {
      return;
    }
    const delta = quill.getContents();

    // Reset all formatting first
    quill.formatText(0, delta.length(), "color", false);

    const regex = /{{(.*?)}}/g;
    const matches = [];

    let index = 0;

    delta.ops.forEach((op) => {
      if (typeof op.insert === "string") {
        let match;
        while ((match = regex.exec(op.insert)) !== null) {
          matches.push({
            start: index + match.index,
            length: match[0].length,
            word: match[1],
          });
        }
        index += op.insert.length;
      } else {
        // Handle embeds like images
        // Assuming each embed occupies a single character in the Delta
        index += 1;
      }
    });

    // Apply formatting to all matches at once
    matches.forEach(({ start, length, word }) => {
      const variable = appliedVariableData?.[word]?.some(
        (item) => typeof item === "string" && item.length > 0,
      );

      quill.formatText(
        start,
        length,
        {
          color: variable
            ? theme.palette.success.main
            : theme.palette.error.main,
        },
        "api", // Use 'api' to prevent recursive triggers
      );
    });
  };

  useEffect(() => {
    const quill = quillRef.current;

    applyFormatting(quill);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appliedVariableData]);

  const getActualLength = (quill) => {
    const blocks = quill.getContents();
    let length = 0;
    blocks.ops.forEach((op) => {
      if (typeof op.insert === "string") {
        length += op.insert.replaceAll("\n", "").length;
      } else if (op.insert.generatePrompt) {
        return;
      } else {
        length += 1;
      }
    });

    return length;
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const debounceUpdatePrompt = useCallback(
    debounce(() => {
      handleLabelsAdd(null);
    }, 300),
    [],
  );

  const onTextChange = (_, __, source) => {
    if (source === "user") {
      const quill = quillRef.current;
      setGeneratePromptVisibility(quill);
      applyFormatting(quill);
    }
    const text = quillRef.current.getSemanticHTML();
    const blocks = parseHtmlToBlocks(text);
    contentField.onChange(blocks);
    if (source === "user") {
      debounceUpdatePrompt();
    }
  };
  const onSelectionChange = () => {};

  const defaultValueRef = useRef(defaultValue);
  const onTextChangeRef = useRef(onTextChange);
  const onSelectionChangeRef = useRef(onSelectionChange);

  useLayoutEffect(() => {
    onTextChangeRef.current = onTextChange;
    onSelectionChangeRef.current = onSelectionChange;
  });

  useEffect(() => {
    const container = containerRef.current;
    const editorContainer = container.appendChild(
      container.ownerDocument.createElement("div"),
    );
    const quill = new Quill(editorContainer, {
      // className : "custom-quill",
      theme: "snow",
      modules: {
        toolbar: false,
      },
      formats: ["color", "customImage", "generatePrompt"],
      placeholder: "Enter Instruction or prompt..",
    });

    quillRef.current = quill;

    if (defaultValueRef.current) {
      quill.setContents(defaultValueRef.current);
    }

    generatePromptId.current = getRandomId();
    quill.insertEmbed(0, "generatePrompt", {
      onGenerate: () => {
        setGeneratePromptOpen(true);
      },
      id: generatePromptId.current,
    });

    setGeneratePromptVisibility(quill);

    quill.on(Quill.events.TEXT_CHANGE, (_, __, source) => {
      onTextChangeRef.current?.(_, __, source);
    });

    quill.on(Quill.events.SELECTION_CHANGE, (...args) => {
      onSelectionChangeRef.current?.(...args);
    });

    applyFormatting(quill);

    return () => {
      quillRef.current = null;
      container.innerHTML = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quillRef]);

  return (
    <Box
      sx={{
        width: "100%",
        border: `1px solid ${theme.palette.divider}`,
        borderRadius: "8px",
        position: "relative",
      }}
    >
      <Box
        sx={{
          padding: "10px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Box>
          <ShowComponent condition={selectRoleVisible}>
            <FormSelectField
              control={control}
              fieldName={roleFieldName}
              size="small"
              options={[
                { value: "user", label: "User" },
                { value: "assistant", label: "Assistant" },
                { value: "system", label: "System" },
              ]}
              onChange={(e) => {
                setGeneratePromptVisibility(quillRef.current, e.target.value);
              }}
            />
          </ShowComponent>
        </Box>
        <Box>
          <ShowComponent condition={onRemove ? index > 1 : null}>
            <CustomTooltip title="Delete" placement="bottom" arrow show>
              <IconButton
                onClick={() => {
                  onRemove();
                }}
              >
                <Iconify
                  icon="solar:trash-bin-trash-bold"
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </CustomTooltip>
          </ShowComponent>
          <ShowComponent condition={onDuplicate}>
            <CustomTooltip title="Duplicate" placement="bottom" arrow show>
              <IconButton
                onClick={() => {
                  onDuplicate(currentContent, currentRole);
                  trackEvent(Events.clickedDuplicatePrompt);
                }}
              >
                <Iconify
                  icon="basil:copy-outline"
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </CustomTooltip>
          </ShowComponent>
          <ShowComponent condition={currentRole === "user"}>
            <CustomTooltip
              title="Upload upto 100 images, 5MB per image"
              placement="bottom"
              arrow
              show
            >
              <IconButton
                // Start of Selection
                onClick={() => {
                  trackEvent(Events.userImageClicked);
                  const input = document.createElement("input");
                  input.type = "file";
                  input.accept = "image/*";
                  input.multiple = true;
                  input.onchange = (event) => {
                    const files = event.target.files;
                    if (files) {
                      Array.from(files).forEach((file) => {
                        const reader = new FileReader();
                        reader.onload = (e) => {
                          const quill = quillRef.current;
                          const range = quill.getSelection();
                          quill.insertEmbed(
                            range ? range.index : 0,
                            "customImage",
                            {
                              src: e.target.result,
                              alt: file.name,
                              id: getRandomId(),
                              setSelectedImageId,
                              removeImageBlot,
                            },
                          );
                        };
                        reader.readAsDataURL(file);
                      });
                    }
                  };
                  input.click();
                }}
              >
                <Iconify
                  icon="solar:gallery-wide-bold"
                  sx={{ color: "text.disabled" }}
                />
              </IconButton>
            </CustomTooltip>
          </ShowComponent>
        </Box>
      </Box>
      <Box ref={containerRef} className="custom-quill" />
      <UploadedImageViewer
        open={Boolean(selectedImageId)}
        onClose={() => setSelectedImageId(null)}
        selectedImageId={selectedImageId}
        removeImageBlot={removeImageBlot}
        replaceImageBlot={replaceImageBlot}
      />
      <Drawer
        anchor="right"
        open={generatePromptOpen}
        onClose={() => setGeneratePromptOpen(false)}
        PaperProps={{
          sx: {
            height: "100vh",
            width: "calc(100% - 450px)",
            position: "fixed",
            zIndex: 1,
            borderRadius: "10px",
            backgroundColor: "background.paper",
          },
        }}
        ModalProps={{
          BackdropProps: {
            style: { backgroundColor: "transparent" },
          },
        }}
      >
        <GeneratePrompt
          open={generatePromptOpen}
          onClose={handleClose}
          onApply={(newContent) => {
            quillRef.current.setContents(
              new Delta().insert(newContent),
              "user",
            );
            setGeneratePromptVisibility(quillRef.current);
            setGeneratePromptOpen(false);
          }}
          setIsDirty={handleSetIsDirty}
        />
        <ConfirmDialog
          content="Are you sure you want to proceed?"
          action={
            <Button
              variant="contained"
              size="small"
              color="error"
              onClick={handleConfirmClose}
            >
              Confirm
            </Button>
          }
          open={confirmOpen}
          onClose={() => {
            setConfirmOpen(false);
          }}
          title="Confirm Action"
          message="Are you sure you want to proceed? Your data will be lost"
        />
      </Drawer>
    </Box>
  );
};

InputPromptBoxV3.propTypes = {
  viewOptions: PropTypes.shape({
    selectRoleVisible: PropTypes.bool,
  }),
  onRemove: PropTypes.func,
  onDuplicate: PropTypes.func,
  control: PropTypes.object,
  roleFieldName: PropTypes.string,
  contentFieldName: PropTypes.string,
  appliedVariableData: PropTypes.object,
  handleLabelsAdd: PropTypes.func,
  index: PropTypes.number,
};

export default InputPromptBoxV3;
