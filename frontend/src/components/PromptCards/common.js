import logger from "src/utils/logger";
import { getRandomId } from "src/utils/utils";
import { normalizeForComparison } from "src/sections/workbench/createPrompt/Playground/common";
import { extractJinjaVariables } from "src/utils/jinjaVariables";
import { PromptRoles } from "src/utils/constants";
import { alpha } from "@mui/material";

const getCursorPosition = (quill) => {
  const currentRange = quill.getSelection();
  return currentRange ? currentRange.index : 0;
};

// Finds a matching key in the variableObject by comparing normalized versions of the keys
// This allows matching variables that differ only in whitespace/special spaces
// e.g. "my variable" would match "my variable" even if one uses non-breaking spaces
const findMatchingVariableKey = (targetVariable, variableObject) => {
  const normalizedTarget = normalizeForComparison(targetVariable);
  return Object.keys(variableObject || {}).find(
    (key) => normalizeForComparison(key) === normalizedTarget,
  );
};

export const placeEditBolt = (
  quill,
  appliedVariableData,
  theme,
  openVariableEditor,
  showEditEmbed,
  allVariablesValid = false,
  variableValidator,
  jinjaMode = false,
) => {
  if (!quill) return;

  let delta = quill.getContents();
  quill.formatText(0, delta.length(), "color", false, "placeBlot");
  quill.formatText(0, delta.length(), "background", false, "placeBlot");
  quill.formatText(0, delta.length(), "bold", false, "placeBlot");
  delta = quill.getContents();

  const regex = /{{(.+?)}}/g;

  let index = 0;
  const matches = [];

  // First remove all existing EditVariable embeds and restore } for {{ }} embeds
  if (showEditEmbed) {
    delta.ops.forEach((op) => {
      if (
        op.insert &&
        typeof op.insert === "object" &&
        op.insert.EditVariable
      ) {
        quill.deleteText(index, 1, "placeBlot");
        // Only restore } for {{ }} embeds; block embeds were inserted without removing a char
        if (!op.insert.EditVariable.fromBlock) {
          quill.insertText(index, "}", "placeBlot");
          index += 1;
        }
      } else if (typeof op.insert === "string") {
        index += op.insert.length;
      } else {
        index += 1;
      }
    });
  }

  // Reset index and get fresh content after removals
  index = 0;
  const newDelta = quill.getContents();

  //   Find all {{ }} matches and their positions
  newDelta.ops.forEach((op) => {
    if (typeof op.insert === "string") {
      let match;
      while ((match = regex.exec(op.insert)) !== null) {
        matches.push({
          start: index + match.index,
          length: match[0].length,
          text: match[0],
          word: match[1],
        });
      }
      index += op.insert.length;
    } else {
      index += 1;
    }
  });

  // In Jinja mode, compute input variables from the full text so we can:
  // 1. Skip loop-scoped / set-scoped variables (e.g. "item" in {% for item in items %})
  // 2. Highlight input variables referenced inside {% %} blocks (for, if, elif, etc.)
  let jinjaInputVars = null;
  if (jinjaMode) {
    const fullText = newDelta.ops
      .filter((op) => typeof op.insert === "string")
      .map((op) => op.insert)
      .join("");
    jinjaInputVars = new Set(extractJinjaVariables(fullText));

    // Scan {% %} blocks for input variable references
    const jinjaBlockRegex = /\{%-?([\s\S]*?)-?%\}/g;
    index = 0;
    newDelta.ops.forEach((op) => {
      if (typeof op.insert === "string") {
        let blockMatch;
        while ((blockMatch = jinjaBlockRegex.exec(op.insert)) !== null) {
          const blockStart = index + blockMatch.index;
          for (const varName of jinjaInputVars) {
            if (!/^\w+$/.test(varName)) continue;
            const varRegex = new RegExp(`\\b${varName}\\b`, "g");
            let varMatch;
            while ((varMatch = varRegex.exec(blockMatch[0])) !== null) {
              matches.push({
                start: blockStart + varMatch.index,
                length: varName.length,
                text: varName,
                word: varName,
                fromBlock: true,
              });
            }
          }
        }
        index += op.insert.length;
      } else {
        index += 1;
      }
    });
  }

  // For block matches, only highlight + embed the first occurrence of each
  // variable name (by document position). Duplicate block references are skipped.
  const firstBlockVars = new Set();
  matches
    .filter((m) => m.fromBlock)
    .sort((a, b) => a.start - b.start)
    .forEach((m) => {
      if (!firstBlockVars.has(m.word)) {
        firstBlockVars.add(m.word);
        m.firstBlock = true;
      }
    });

  // Process matches in reverse document order so embed insertions don't shift
  // positions of matches that haven't been processed yet.
  const sortedMatches = [...matches].sort((a, b) => b.start - a.start);
  sortedMatches.forEach(({ start, length, word, fromBlock, firstBlock }) => {
    // For block matches, skip duplicates — only process the first occurrence
    if (fromBlock && !firstBlock) return;
    // In Jinja mode, skip variables that are not real inputs (e.g. loop vars)
    // Use root name for dotted access (e.g. "data.name" → check "data")
    if (jinjaInputVars) {
      const rootVar = word.trim().split(".")[0];
      if (!jinjaInputVars.has(rootVar)) {
        return;
      }
    }
    // When allVariablesValid is true, treat every variable as valid (green)
    if (allVariablesValid) {
      quill.formatText(
        start,
        length,
        {
          color: theme.palette.green[800],
          background:
            theme.palette.mode === "dark"
              ? alpha(theme.palette.green[200], 0.1)
              : theme.palette.green[50],
          bold: true,
        },
        "placeBlot",
      );
      return;
    }

    // When a custom validator function is provided, use it instead of appliedVariableData.
    // Validator may return true (valid/green), false (invalid/red), or null (skip highlighting).
    if (typeof variableValidator === "function") {
      const isValid = variableValidator(word);
      if (isValid === null) return; // skip — not a user-facing variable
      quill.formatText(
        start,
        length,
        {
          color: isValid
            ? "var(--mention-valid-color)"
            : "var(--mention-invalid-color)",
          background: isValid
            ? "var(--mention-valid-bg)"
            : "var(--mention-invalid-bg)",
          bold: true,
        },
        "placeBlot",
      );
      return;
    }

    // Find the matching variable key in appliedVariableData that corresponds to this variable word
    const matchingKey = findMatchingVariableKey(word, appliedVariableData);

    // Check if the variable exists and has at least one non-empty string value
    // This determines if the variable is properly defined with content
    const variable =
      matchingKey &&
      appliedVariableData[matchingKey]?.some((item) => {
        if (typeof item === "string") {
          return item.trim().length > 0;
        }
        if (typeof item === "number") {
          return !isNaN(item) && isFinite(item); // Check for valid numbers
        }
        return false;
      });

    if (!variable && showEditEmbed) {
      if (!fromBlock) {
        // {{ }} match: replace the closing } with the embed
        quill.deleteText(start + length - 1, 1, "placeBlot");
        quill.insertEmbed(
          start + length - 1,
          "EditVariable",
          { openVariableEditor },
          "placeBlot",
        );

        const cursorPosition = getCursorPosition(quill);
        const isCursorAtPosition = cursorPosition === start + length - 1;
        if (isCursorAtPosition) {
          setTimeout(() => {
            quill.setSelection(start + length + 100, 0, "placeBlot");
          }, 0);
        }
      } else {
        // {% %} block match: insert the embed after the first occurrence only
        quill.insertEmbed(
          start + length,
          "EditVariable",
          { openVariableEditor, fromBlock: true },
          "placeBlot",
        );
      }
    }

    // Format the variable name text — for {{ }} embeds the } was replaced so use length - 1
    const fmtLength =
      !variable && showEditEmbed && !fromBlock ? length - 1 : length;

    quill.formatText(
      start,
      fmtLength,
      {
        color: variable
          ? "var(--mention-valid-color)"
          : "var(--mention-invalid-color)",
        background: variable
          ? "var(--mention-valid-bg)"
          : "var(--mention-invalid-bg)",
        bold: true,
      },
      "placeBlot",
    );
  });
};

export const handleRemoveEditVariable = (quill) => {
  if (!quill) return;

  let delta = quill.getContents();
  quill.formatText(0, delta.length(), "color", false, "placeBlot");
  quill.formatText(0, delta.length(), "background", false, "placeBlot");
  quill.formatText(0, delta.length(), "bold", false, "placeBlot");
  delta = quill.getContents();

  let index = 0;

  // First remove all existing EditVariable embeds and restore } for {{ }} embeds
  delta.ops.forEach((op) => {
    if (op.insert && typeof op.insert === "object" && op.insert.EditVariable) {
      quill.deleteText(index, 1, "placeBlot");
      if (!op.insert.EditVariable.fromBlock) {
        quill.insertText(index, "}", "placeBlot");
        index += 1;
      }
    } else if (typeof op.insert === "string") {
      index += op.insert.length;
    } else {
      index += 1;
    }
  });
};

export const handleRemoveImage = (quill) => (imageId) => {
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
};

export const handleRemoveAudio = (quill) => (audioId) => {
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
};

export const handleRemoveAllImages = (quill) => {
  if (!quill) return;
  const delta = quill.getContents();
  let index = 0;
  const deleteOps = [];

  // First pass: find all embeds with the target format
  for (let i = 0; i < delta.ops.length; i++) {
    const op = delta.ops[i];

    if (op.insert && op.insert.ImageBlot) {
      // Store both the index and the length (usually 1 for embeds)
      deleteOps.push({ index, length: 1 });
    }

    // Advance the index based on content type
    if (typeof op.insert === "string") {
      index += op.insert.length;
    } else {
      index += 1; // Embeds count as 1 position
    }
  }

  // Second pass: delete the embeds in reverse order
  // This is crucial to maintain correct indices
  for (let i = deleteOps.length - 1; i >= 0; i--) {
    const { index, length } = deleteOps[i];
    quill.deleteText(index, length);
  }
};

export const handleReplaceImage =
  (quill) => (imageId, newImageData, setSelectedImageId) => {
    if (!quill) return;

    // Find the index of the image to replace
    const delta = quill.getContents();
    let index = 0;
    let found = false;

    for (let i = 0; i < delta.ops.length; i++) {
      const op = delta.ops[i];
      if (op.insert?.ImageBlot && op.insert.ImageBlot.id === imageId) {
        found = true;
        break;
      }
      index += typeof op.insert === "string" ? op.insert.length : 1;
    }

    if (!found) return;

    handleRemoveImage(quill)(imageId);

    embedImage(newImageData, quill, index, setSelectedImageId);
  };

export const embedImage = (image, quill, location, setSelectedImage) => {
  try {
    quill.insertEmbed(
      location,
      "ImageBlot",
      {
        url: image?.url,
        name: image?.img_name,
        size: image?.img_size,
        setSelectedImage,
        id: getRandomId(),
        handleRemoveImage: handleRemoveImage(quill),
      },
      "api",
    );
  } catch (error) {
    logger.error("Failed to embed image", error);
  }
};

export const embedImages = (images, quill, setSelectedImage, location) => {
  let insertAt = location;
  for (const image of images) {
    insertAt = insertAt ? insertAt : 0;
    embedImage(image, quill, insertAt, setSelectedImage);
    insertAt;
  }
};

export const embedAudio = (audio, quill, location) => {
  try {
    quill.insertEmbed(
      location,
      "AudioBlot",
      {
        url: audio?.url,
        name: audio?.audio_name,
        size: audio?.audio_size,
        mimeType: audio?.audio_type,
        id: getRandomId(),
        handleRemoveAudio: handleRemoveAudio(quill),
      },
      "api",
    );
  } catch (error) {
    logger.error("Failed to embed audio", error);
  }
};

export const embedAudios = (audios, quill, location) => {
  let insertAt = location;
  for (const audio of audios) {
    insertAt = insertAt ? insertAt : 0;
    embedAudio(audio, quill, insertAt);
    insertAt;
  }
};

export const handleRemovePdf = (quill) => (pdfId) => {
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
};

export const embedPdf = (pdf, quill, location) => {
  try {
    quill.insertEmbed(
      location,
      "PdfBlot",
      {
        url: pdf?.url,
        name: pdf?.pdf_name,
        size: pdf?.pdf_size,
        id: getRandomId(),
        handleRemovePdf: handleRemovePdf(quill),
      },
      "api",
    );
  } catch (error) {
    logger.error("Failed to embed pdf", error);
  }
};

export const embedPdfs = (pdfs, quill, location) => {
  let insertAt = location;
  for (const pdf of pdfs) {
    insertAt = insertAt ? insertAt : 0;
    embedPdf(pdf, quill, insertAt);
    insertAt;
  }
};

export const getBlocks = (quill) => {
  const blocks = [];
  const delta = quill.getContents();
  let lastString = "";
  let stringAbrupt = false;
  let imageObject = null;
  let audioObject = null;
  let pdfObject = null;

  for (let i = 0; i < delta.ops.length; i++) {
    const op = delta.ops[i];
    if (typeof op.insert === "string") {
      stringAbrupt = false;
      lastString += op.insert;
    } else if (
      op.insert &&
      typeof op.insert === "object" &&
      op.insert.EditVariable
    ) {
      stringAbrupt = false;
      if (!op.insert.EditVariable.fromBlock) {
        lastString += "}";
      }
    } else if (
      op.insert &&
      typeof op.insert === "object" &&
      op.insert.ImageBlot?.imageData?.url
    ) {
      stringAbrupt = true;
      imageObject = op.insert?.ImageBlot?.imageData;
    } else if (
      op.insert &&
      typeof op.insert === "object" &&
      op.insert.AudioBlot?.audioData?.url
    ) {
      stringAbrupt = true;
      audioObject = op.insert?.AudioBlot?.audioData;
    } else if (
      op.insert &&
      typeof op.insert === "object" &&
      op.insert.PdfBlot?.pdfData?.url
    ) {
      stringAbrupt = true;
      pdfObject = op.insert?.PdfBlot?.pdfData;
    }

    if (lastString?.length > 0 && stringAbrupt) {
      blocks.push({
        type: "text",
        text: lastString,
      });
      lastString = "";
      stringAbrupt = false;
    }
    if (imageObject) {
      blocks.push({
        type: "image_url",
        imageUrl: imageObject,
      });
      imageObject = null;
    }
    if (audioObject) {
      blocks.push({
        type: "audio_url",
        audioUrl: audioObject,
      });
      audioObject = null;
    }
    if (pdfObject) {
      blocks.push({
        type: "pdf_url",
        pdfUrl: { ...pdfObject, file_name: pdfObject.pdf_name },
      });
      pdfObject = null;
    }
  }

  if (lastString?.length > 0) {
    blocks.push({
      type: "text",
      text: lastString,
    });
    lastString = "";
    stringAbrupt = false;
  }

  return blocks;
};

export const PROMPT_EDITOR_OPTIONS = [
  {
    name: "copy",
    label: "Copy",
    icon: "/assets/icons/ic_copy.svg",
  },
  {
    name: "maximize",
    label: "Maximize",
    icon: "/assets/icons/ic_maximize.svg",
  },
  {
    name: "delete",
    label: "Delete",
    icon: "/assets/icons/components/ic_delete.svg",
    color: "red.600",
  },
];

export function getPromptRoleOptions(hasSystemRole, allowAllRoleChange) {
  const roles = [
    PromptRoles.USER,
    PromptRoles.ASSISTANT,
    ...(!hasSystemRole && allowAllRoleChange ? [PromptRoles.SYSTEM] : []),
  ];

  return roles;
}
