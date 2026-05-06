import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
} from "react";
import Quill from "quill";
import "./PromptCardEditor.css";
import PropTypes from "prop-types";
import { Box, useTheme } from "@mui/material";
import {
  getBlocks,
  handleRemoveEditVariable,
  placeEditBolt,
  handleRemoveAllImages,
} from "./common";
import { PromptContentTypes } from "src/utils/constants";
import EditVariableBolt from "./Blots/EditVariableBlot";
import ImageBlot from "./Blots/ImageBlot";
import AudioBlot from "./Blots/AudioBlot";
import { getRandomId } from "src/utils/utils";
import "quill-mention/dist/quill.mention.css";
import "quill-mention"; // Ensure it's available globally
import "quill-mention/autoregister";
import PdfBlot from "./Blots/PdfBlot";

Quill.register("formats/EditVariable", EditVariableBolt);
Quill.register("formats/ImageBlot", ImageBlot);
Quill.register("formats/AudioBlot", AudioBlot);
Quill.register("formats/PdfBlot", PdfBlot);

const expandableCSS = {
  minHeight: "30px",
  maxHeight: "400px",
  overflowY: "auto",
};

const PromptEditor = React.forwardRef(
  (
    {
      placeholder,
      appliedVariableData,
      prompt,
      onPromptChange,
      openVariableEditor,
      onSelectionChange,
      setSelectedImage,
      dropdownOptions = [],
      showEditEmbed = true,
      mentionEnabled = false,
      mentionDenotationChars,
      onMentionSelect,
      allowVariables = true,
      inputRef,
      disabled,
      expandable,
      label,
      sx,
      allVariablesValid = false,
      variableValidator,
      jinjaMode = false,
    },
    quillRef,
  ) => {
    const previousAppliedVariableData = useRef(appliedVariableData);

    const containerRef = useRef(null);

    const handleRemoveImage = useCallback(
      (imageId) => {
        const quill = quillRef.current;
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
      [quillRef],
    );

    const handleRemoveAudio = useCallback(
      (audioId) => {
        const quill = quillRef.current;

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
      [quillRef],
    );

    const handleRemovePdf = useCallback(
      (pdfId) => {
        const quill = quillRef.current;

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
      [quillRef],
    );

    const defaultValue = useMemo(() => {
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
                setSelectedImage,
                id: getRandomId(),
                handleRemoveImage,
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

      return delta;
    }, [
      handleRemoveAudio,
      handleRemoveImage,
      handleRemovePdf,
      prompt,
      setSelectedImage,
    ]);

    const defaultValueRef = useRef(defaultValue);

    // Sync Quill content when prompt prop changes after initial mount
    // (e.g. async API load, or an AI replacement that rewrites the whole text).
    // The prevPromptTextRef dedup prevents the normal typing loop: when the
    // user types, our own onPromptChange bubbles up and comes back as a new
    // prompt prop — but the derived newText matches prevPromptTextRef so we
    // short-circuit before touching Quill.
    const hasInitializedRef = useRef(false);
    const prevPromptTextRef = useRef("");
    useEffect(() => {
      if (!hasInitializedRef.current) {
        hasInitializedRef.current = true;
        return;
      }
      // Normalize trailing newlines on BOTH sides of the comparison — Quill
      // always keeps a trailing "\n" in its document, and getBlocks() carries
      // that newline into the block text, so a raw compare would always miss.
      const rawNewText = prompt?.map((b) => b.text || "").join("") || "";
      const newText = rawNewText.replace(/\n+$/, "");
      if (newText === prevPromptTextRef.current) return;
      prevPromptTextRef.current = newText;

      const quill = quillRef.current;
      if (!quill) return;
      // Use getBlocks() instead of getText() to normalize EditVariable embeds
      // back to "}" — getText() returns \uFFFC for embeds, causing a false
      // mismatch that triggers setContents and resets the cursor to position 0.
      const currentBlocks = getBlocks(quill);
      const currentText = currentBlocks
        .map((b) => b.text || "")
        .join("")
        .replace(/\n+$/, "");
      if (currentText === newText) return;
      quill.setContents(defaultValue, "api");
      defaultValueRef.current = defaultValue;

      // setContents drops all formatting, so re-apply variable coloring /
      // validator styling to any {{variable}} tokens in the new content.
      if (allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [prompt]); // depend on prompt (string content), not defaultValue (new object each render)

    const theme = useTheme();

    useEffect(() => {
      if (
        JSON.stringify(previousAppliedVariableData.current) !==
        JSON.stringify(appliedVariableData)
      ) {
        const quill = quillRef.current;

        if (allowVariables) {
          placeEditBolt(
            quill,
            appliedVariableData,
            theme,
            openVariableEditor,
            showEditEmbed,
            allVariablesValid,
            variableValidator,
            jinjaMode,
          );
        }
        previousAppliedVariableData.current = appliedVariableData;
      }
    }, [appliedVariableData]);

    useEffect(() => {
      const quill = quillRef.current;
      if (allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      } else {
        handleRemoveEditVariable(quill);
        handleRemoveAllImages(quill);
      }
    }, [allowVariables]);

    // Re-validate when variableValidator changes (e.g., global variables API returns)
    useEffect(() => {
      if (!variableValidator) return;
      const quill = quillRef.current;
      if (quill && allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }
    }, [variableValidator]);

    // Re-highlight when template format changes (mustache ↔ jinja)
    useEffect(() => {
      const quill = quillRef.current;
      if (quill && allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }
    }, [jinjaMode]);

    const onTextChange = (_, __, source) => {
      const quill = quillRef.current;
      if (source === "user" && allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }
      if (source === "placeBlot") return;
      const blocks = getBlocks(quill);
      onPromptChange(blocks);
    };

    const onTextChangeRef = useRef(onTextChange);

    const onSelectionChangeRef = useRef(onSelectionChange);

    useLayoutEffect(() => {
      onTextChangeRef.current = onTextChange;
    });

    useEffect(() => {
      const formats = [
        "color",
        "background",
        "ImageBlot",
        "AudioBlot",
        "PdfBlot",
        "bold",
      ];
      if (showEditEmbed) {
        formats.push("EditVariable");
      }
      const container = containerRef.current;
      const editorContainer = container.appendChild(
        container.ownerDocument.createElement("div"),
      );

      // const formats = ["color", "ImageBlot", "EditVariable"];

      const quill = new Quill(editorContainer, {
        theme: "snow",
        readOnly: disabled,
        modules: {
          toolbar: false,
          clipboard: {
            matchers: [
              [
                Node.TEXT_NODE,
                (node, delta) => {
                  // Preserve leading whitespace (spaces, tabs) and non-newline whitespace
                  if (node.data.match(/[^\\n\\S]|\\t/)) {
                    const Delta = Quill.import("delta");
                    return new Delta().insert(node.data);
                  }
                  return delta;
                },
              ],
            ],
          },
          mention: {
            // Include . [ ] and digits for JSON path support (e.g., input.config.items[0].name)
            allowedChars: /^[A-Za-z0-9_.[[\]\sÅÄÖåäö]*$/,
            mentionDenotationChars: mentionDenotationChars || ["{{"],
            // Fix: Attach mention list to body instead of container
            ...(expandable && {
              positioningStrategy: "fixed",
            }),
            source: function (searchTerm, renderList) {
              if (!mentionEnabled) {
                renderList([], searchTerm);
                return;
              }

              const trimmedTerm = searchTerm.trim().toLowerCase();
              let matches = dropdownOptions;

              if (trimmedTerm.length > 0) {
                // Check if user is typing a dot after a column name (e.g., "input.")
                const lastDotIndex = trimmedTerm.lastIndexOf(".");

                if (lastDotIndex > 0) {
                  // User typed "columnName." - filter to JSON paths for that column
                  const baseColumn = trimmedTerm.substring(0, lastDotIndex);
                  const pathPart = trimmedTerm.substring(lastDotIndex + 1);

                  matches = dropdownOptions.filter((item) => {
                    if (!item.isJsonPath) return false;
                    const itemLower = item.value.toLowerCase();
                    // Match if starts with baseColumn. and (pathPart is empty or matches)
                    return (
                      itemLower.startsWith(baseColumn + ".") &&
                      (pathPart.length === 0 || itemLower.includes(trimmedTerm))
                    );
                  });
                } else {
                  // Normal search - include both base columns and matching JSON paths
                  matches = dropdownOptions.filter((item) =>
                    item.value.toLowerCase().includes(trimmedTerm),
                  );
                }
              }

              renderList(matches, searchTerm);
            },
            renderItem(item) {
              if (!mentionEnabled) return "";
              // Style JSON paths with a different color to distinguish them
              if (item.isJsonPath) {
                return `<span style="color: var(--primary-main)">${item.value}</span>`;
              }
              // Add indicator for JSON-type columns that have expandable paths
              if (item.dataType === "json") {
                return `${item.value} <span style="color: #999; font-size: 10px;">{ }</span>`;
              }
              return `${item.value}`;
            },
            onSelect(item) {
              if (!mentionEnabled) return;

              // Allow custom handler (e.g. for Jinja {% %} blocks)
              if (onMentionSelect) {
                onMentionSelect(item, quillRef?.current, dropdownOptions);
                return;
              }

              const quill = quillRef?.current;

              const cursorPosition = quill.getSelection(true).index;
              const textBefore = quill.getText(0, cursorPosition);
              // Updated pattern to match JSON paths: {{input.path.to.value or {{input test
              // eslint-disable-next-line no-useless-escape
              const match = textBefore.match(/{{[\w.\s\[\]]*$/);

              if (match) {
                const startIndex = cursorPosition - match[0].length;
                const textAfter = quill.getText(cursorPosition, 2);
                const hasClosingBraces = textAfter === "}}";
                const deleteLength =
                  match[0].length + (hasClosingBraces ? 2 : 0);

                quill.deleteText(startIndex, deleteLength);
                const isValid = dropdownOptions.some(
                  (v) => v.value.toLowerCase() === item.value.toLowerCase(),
                );

                quill.insertText(
                  startIndex,
                  `{{${item?.value}}}`,
                  {
                    color: isValid
                      ? "var(--mention-valid-color)"
                      : "var(--mention-invalid-color)",
                  },
                  "user",
                );
                quill.setSelection(startIndex + item?.value?.length + 4);
              }
            },
          },
        },
        formats,
        placeholder: placeholder,
      });
      quill.root.setAttribute("spellcheck", false);

      quillRef.current = quill;

      if (typeof inputRef === "function") {
        setTimeout(() => {
          inputRef({
            focus: () => {
              const quill = quillRef?.current;
              const containerEl = containerRef?.current;

              if (!quill) return;

              const range = quill.getSelection();
              const length = quill.getLength();

              quill.focus();

              if (!range) {
                quill.setSelection(length, 0);
              } else {
                quill.setSelection(range.index, range.length);
              }

              // Scroll container into view after focusing
              if (containerEl) {
                setTimeout(() => {
                  containerEl.scrollIntoView({
                    behavior: "smooth",
                    block: "center", // or "start" depending on your layout
                  });
                }, 20); // delay to allow focus/render updates
              }
            },
          });
        }, 0);
      }

      if (defaultValueRef.current) {
        quill.setContents(defaultValueRef.current);
      }

      quill.on(Quill.events.TEXT_CHANGE, (...args) => {
        onTextChangeRef.current?.(...args);
      });

      quill.on(Quill.events.SELECTION_CHANGE, (...args) => {
        onSelectionChangeRef.current?.(...args);
      });

      if (allowVariables) {
        placeEditBolt(
          quill,
          appliedVariableData,
          theme,
          openVariableEditor,
          showEditEmbed,
          allVariablesValid,
          variableValidator,
          jinjaMode,
        );
      }

      return () => {
        // Close mention dropdown before unmounting to prevent orphaned DOM elements
        const mentionModule = quillRef.current?.getModule("mention");
        if (mentionModule) {
          mentionModule.hideMentionList();
        }

        quillRef.current = null;
        container.innerHTML = "";

        if (typeof inputRef === "function") {
          inputRef(null); // clean up
        }
      };
    }, [quillRef, placeholder]);

    useEffect(() => {
      const quill = quillRef?.current;
      if (!quill) return;

      const mentionModule = quill.getModule("mention");
      if (!mentionModule) return;

      // Update the mention source dynamically
      mentionModule.options.source = function (searchTerm, renderList) {
        if (!mentionEnabled) {
          renderList([], searchTerm);
          return;
        }

        const matches =
          searchTerm.trim().length === 0
            ? dropdownOptions
            : dropdownOptions.filter((item) =>
                item.value
                  .toLowerCase()
                  .includes(searchTerm.trim().toLowerCase()),
              );

        renderList(matches, searchTerm);
      };

      // Update renderItem if needed
      mentionModule.options.renderItem = (item) =>
        mentionEnabled ? `${item.value}` : "";
    }, [dropdownOptions, mentionEnabled, quillRef]);

    return (
      <div
        className={`prompt-editor-wrapper ${!expandable && "responsive-container"}`}
      >
        {label ? (
          <label className="floating-label">
            {typeof label === "boolean" ? "Prompt" : `${label} Prompt`}
          </label>
        ) : null}

        <Box
          className="prompt-editor-card"
          ref={containerRef}
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "4px",
            padding: "12px 16px",
            width: "100%",
            ...(expandable ? expandableCSS : {}),
            ...sx,
          }}
        >
          {/* Your content here */}
        </Box>
      </div>
    );
  },
);

PromptEditor.displayName = "PromptEditor";

PromptEditor.propTypes = {
  placeholder: PropTypes.string,
  appliedVariableData: PropTypes.object,
  prompt: PropTypes.array,
  onPromptChange: PropTypes.func,
  openVariableEditor: PropTypes.func,
  onSelectionChange: PropTypes.func,
  setSelectedImage: PropTypes.func,
  dropdownOptions: PropTypes.array,
  showEditEmbed: PropTypes.bool,
  mentionEnabled: PropTypes.bool,
  mentionDenotationChars: PropTypes.array,
  onMentionSelect: PropTypes.func,
  allowVariables: PropTypes.bool,
  inputRef: PropTypes.object,
  disabled: PropTypes.bool,
  expandable: PropTypes.bool,
  label: PropTypes.string,
  sx: PropTypes.object,
  allVariablesValid: PropTypes.bool,
  variableValidator: PropTypes.func,
  jinjaMode: PropTypes.bool,
};

export default PromptEditor;
