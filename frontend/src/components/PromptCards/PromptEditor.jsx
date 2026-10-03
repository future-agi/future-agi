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
  normalizeContentBlocks,
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
import { useSnackbar } from "src/components/snackbar";
import { setEmbedCallbacks } from "./Blots/embedCallbacks";
import { MEDIA_EMBED_CLASS } from "./Blots/MediaBlockEmbed";
import { createPromptClipboardHandlers } from "./clipboard/promptClipboardHandlers";
import { useClipboardProvenance } from "./clipboard/useClipboardProvenance";
import {
  EMBED_CONTROL_SELECTOR,
  EMBED_NODE_SELECTOR,
  embedIndexForNode,
  embedIndicesInRange,
  leafIsMediaEmbed,
  selectAllRange,
} from "./clipboard/selectionHelpers";
import { ALL_MEDIA_KINDS, OMISSION_MESSAGES } from "./clipboard/constants";

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
      allowedMediaTypes = ALL_MEDIA_KINDS,
    },
    quillRef,
  ) => {
    const previousAppliedVariableData = useRef(appliedVariableData);

    const containerRef = useRef(null);

    // TH-150: removing an attachment card edits only this editor's document
    // (no storage request), is a single undoable user step, leaves the caret
    // where the card was, and is refused in read-only editors.
    const removeEmbedAt = useCallback((quill, index) => {
      if (!quill || !quill.isEnabled()) return;
      quill.deleteText(index, 1, "user");
      quill.setSelection(index, 0, "silent");
    }, []);

    const handleRemoveImage = useCallback(
      (imageId) => {
        const quill = quillRef.current;
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
          removeEmbedAt(quill, index);
        }
      },
      [quillRef, removeEmbedAt],
    );

    const handleRemoveAudio = useCallback(
      (audioId) => {
        const quill = quillRef.current;
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
          removeEmbedAt(quill, index);
        }
      },
      [quillRef, removeEmbedAt],
    );

    const handleRemovePdf = useCallback(
      (pdfId) => {
        const quill = quillRef.current;
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
          removeEmbedAt(quill, index);
        }
      },
      [quillRef, removeEmbedAt],
    );

    const defaultValue = useMemo(() => {
      const blocks = normalizeContentBlocks(prompt) || [];
      const delta = { ops: [] };

      blocks.forEach((block) => {
        if (block.type === PromptContentTypes.IMAGE_URL) {
          // Add image embed
          delta.ops.push({
            insert: {
              ImageBlot: {
                url: block?.image_url?.url,
                name: block?.image_url?.img_name,
                size: block?.image_url?.img_size,
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
                url: block?.audio_url?.url,
                name: block?.audio_url?.audio_name,
                size: block?.audio_url?.audio_size,
                mimeType: block?.audio_url?.audio_type,
                id: getRandomId(),
                handleRemoveAudio,
              },
            },
          });
        } else if (block.type === PromptContentTypes.PDF_URL) {
          delta.ops.push({
            insert: {
              PdfBlot: {
                url: block?.pdf_url?.url,
                name: block?.pdf_url?.file_name,
                size: block?.pdf_url?.pdf_size,
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

    // TH-150: clipboard provenance, omission notices and per-editor embed
    // callbacks. The Quill handlers live outside React render, so they read
    // the latest values through refs.
    const getProvenance = useClipboardProvenance();
    const { enqueueSnackbar } = useSnackbar();
    const notifyRef = useRef(null);
    notifyRef.current = (reason) => {
      const message = OMISSION_MESSAGES[reason] || OMISSION_MESSAGES.invalid;
      enqueueSnackbar?.(message, { variant: "warning" });
    };
    const allowedMediaTypesRef = useRef(allowedMediaTypes);
    allowedMediaTypesRef.current = allowedMediaTypes;
    const embedCallbacksRef = useRef({});
    useLayoutEffect(() => {
      embedCallbacksRef.current = {
        handleRemoveImage,
        handleRemoveAudio,
        handleRemovePdf,
        setSelectedImage,
        openVariableEditor,
        readOnly: Boolean(disabled),
      };
      if (quillRef.current) {
        setEmbedCallbacks(quillRef.current, embedCallbacksRef.current);
      }
    });

    // `readOnly` is only read by the Quill constructor; keep the instance in
    // sync when the prop changes and re-render cards so mutating controls
    // appear/disappear with it (REQ-11).
    useEffect(() => {
      const quill = quillRef.current;
      if (!quill) return;
      quill.enable(!disabled);
      setEmbedCallbacks(quill, embedCallbacksRef.current);
      quill.root.querySelectorAll(`.${MEDIA_EMBED_CLASS}`).forEach((node) => {
        const blot = Quill.find(node, true);
        if (blot && typeof blot.renderCard === "function") blot.renderCard();
      });
    }, [disabled, quillRef]);

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
          // TH-150: select-all scoped to this editor; one key removes exactly
          // one adjacent attachment. Custom bindings run before Quill's
          // defaults for the same key; returning true falls through to them.
          keyboard: {
            bindings: {
              selectAll: {
                key: "a",
                shortKey: true,
                handler() {
                  const range = selectAllRange(this.quill);
                  this.quill.setSelection(range.index, range.length, "user");
                  return false;
                },
              },
              deleteEmbedBackward: {
                key: "Backspace",
                collapsed: true,
                handler(range) {
                  if (!this.quill.isEnabled()) return false;
                  if (range.index === 0) return true;
                  if (!leafIsMediaEmbed(this.quill, range.index - 1))
                    return true;
                  this.quill.deleteText(range.index - 1, 1, "user");
                  this.quill.setSelection(range.index - 1, 0, "silent");
                  return false;
                },
              },
              deleteEmbedForward: {
                key: "Delete",
                collapsed: true,
                handler(range) {
                  if (!this.quill.isEnabled()) return false;
                  if (!leafIsMediaEmbed(this.quill, range.index)) return true;
                  this.quill.deleteText(range.index, 1, "user");
                  this.quill.setSelection(range.index, 0, "silent");
                  return false;
                },
              },
            },
          },
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
      setEmbedCallbacks(quill, embedCallbacksRef.current);

      // TH-150: own copy/cut/paste on this editor (capture phase, see module).
      const clipboardHandlers = createPromptClipboardHandlers({
        quill,
        getProvenance,
        getAllowedMediaTypes: () => allowedMediaTypesRef.current,
        notify: (reason) => notifyRef.current?.(reason),
        makeId: getRandomId,
      });
      clipboardHandlers.attach();

      // Clicking an attachment card (not its buttons) selects it as one block
      // and keeps focus inside the editor, so a following Cmd/Ctrl+A,
      // Backspace or Cmd/Ctrl+C acts on this editor rather than the page.
      const onEmbedMouseDown = (event) => {
        const target = event.target;
        if (!target || typeof target.closest !== "function") return;
        const embedNode = target.closest(EMBED_NODE_SELECTOR);
        if (!embedNode || !quill.root.contains(embedNode)) return;
        if (target.closest(EMBED_CONTROL_SELECTOR)) return;
        const index = embedIndexForNode(quill, embedNode);
        if (index < 0) return;
        event.preventDefault();
        quill.focus();
        quill.setSelection(index, 1, "user");
      };
      quill.root.addEventListener("mousedown", onEmbedMouseDown);

      // Visual selected state for atomic cards (their text is not selectable).
      const syncSelectedEmbeds = (range) => {
        quill.root
          .querySelectorAll(`.${MEDIA_EMBED_CLASS}.is-selected`)
          .forEach((node) => node.classList.remove("is-selected"));
        if (!range || range.length === 0) return;
        embedIndicesInRange(quill.getContents(), range).forEach(({ index }) => {
          const [leaf] = quill.getLeaf(index);
          leaf?.domNode?.classList?.add("is-selected");
        });
      };

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
        // silent caret moves after delete/paste emit no selection-change
        syncSelectedEmbeds(quill.getSelection());
      });

      quill.on(Quill.events.SELECTION_CHANGE, (...args) => {
        syncSelectedEmbeds(args[0]);
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

        clipboardHandlers.detach();
        quill.root.removeEventListener("mousedown", onEmbedMouseDown);

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
  allowedMediaTypes: PropTypes.arrayOf(PropTypes.oneOf(ALL_MEDIA_KINDS)),
};

export default PromptEditor;
