import { format, formatISO } from "date-fns";
import React, { useCallback, useEffect, useRef } from "react";
import { palette } from "src/theme/palette";

import { extractJinjaVariables } from "./jinjaVariables";
import { logger } from "./logger";

export const colorPalette = [
  { bgColor: "#ECE8FF", textColor: "#846EFF", graphBgColor: "#D7D0FF" },
  { bgColor: "#FDEEE2", textColor: "#F17F2B", graphBgColor: "#FBD8BF" },
  { bgColor: "#E0F7EC", textColor: "#26BD78", graphBgColor: "#B3EAD1" },
  { bgColor: "#E8F3FE", textColor: "#559FF7", graphBgColor: "#CFE5FD" },
  { bgColor: "#F9E8FD", textColor: "#D65EEC", graphBgColor: "#F2D1FA" },
  { bgColor: "#ECE8FF", textColor: "#573FCC", graphBgColor: "#B8AFFF" },
  { bgColor: "#FDEEE2", textColor: "#B5520A", graphBgColor: "#F8B98A" },
  { bgColor: "#E0F7EC", textColor: "#007C3E", graphBgColor: "#80DBB3" },
  { bgColor: "#E8F3FE", textColor: "#276DBD", graphBgColor: "#A3CDFB" },
  { bgColor: "#ECE8FF", textColor: "#9F46B5", graphBgColor: "#E8A9F5" },
];

const nameToDMap = new Map(); // Store name-to-label mappings
let dCounter = 1; // Counter for D labels
const nameToColorMap = new Map(); // Store name-to-color mappings

export function getCompareFixedDLabel(name) {
  if (!name) {
    return "D1";
  }

  if (nameToDMap.has(name)) {
    return nameToDMap.get(name);
  }

  const label = `D${dCounter}`;
  nameToDMap.set(name, label);
  dCounter++;

  return label;
}

export function getCompareSequentialColor(name) {
  if (!name) {
    return colorPalette[0];
  } // Default color

  if (nameToColorMap.has(name)) {
    return nameToColorMap.get(name);
  }

  const color = colorPalette[colorCounter % colorPalette.length]; // Cycle through colors
  nameToColorMap.set(name, color);
  colorCounter++;

  return color;
}

export function resetDLabels() {
  nameToDMap.clear();
  nameToColorMap.clear();
  dCounter = 1;
  colorCounter = 0;
}

const colorAssignmentMap = new Map(); // Store assigned colors for each unique seed
let colorCounter = 0; // Track the next color index

export function getColorCompareSeed(seed) {
  if (!seed) {
    return colorPalette[0];
  } // Default color if no seed provided

  // If the seed already has an assigned color, return it
  if (colorAssignmentMap.has(seed)) {
    return colorAssignmentMap.get(seed);
  }

  // Assign the next available color in sequence
  const assignedColor = colorPalette[colorCounter % colorPalette.length];

  // Store the assigned color for future reference
  colorAssignmentMap.set(seed, assignedColor);

  // Increment counter for the next assignment
  colorCounter++;

  return assignedColor;
}

export function indexToLetter(index) {
  // Check if the input is a non-negative integer
  if (index < 0 || index > 25 || !Number.isInteger(index)) {
    return "Invalid index";
  }
  // Convert the index to the corresponding uppercase letter
  return String.fromCharCode(65 + index);
}

export function getPerformanceTagColor(tag) {
  return tag.toLowerCase().includes("positive") ? "success" : "error";
}

const ColorPairs = [
  {
    tagBackground: palette("light").pink["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").pink[100],
  },
  {
    tagBackground: palette("light").blue["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").blue[100],
  },
  {
    tagBackground: palette("light").orange["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").orange[100],
  },
  {
    tagBackground: palette("light").green["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").green[100],
  },
  {
    tagBackground: palette("light").purple["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").purple[100],
  },
  {
    tagBackground: palette("light").pink["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").pink[200],
  },
  {
    tagBackground: palette("light").blue["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").blue[200],
  },
  {
    tagBackground: palette("light").orange["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").orange[200],
  },
  {
    tagBackground: palette("light").green["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").green[200],
  },
  {
    tagBackground: palette("light").purple["400"],
    tagForeground: palette("light").whiteScale[100],
    solid: palette("light").purple[200],
  },
];

export const getUniqueColorPalette = (idx) => {
  return ColorPairs[idx % ColorPairs.length];
};

export function interpolateColorBasedOnScore(
  score,
  maxScore = 10,
  reverse = false,
) {
  if (score < 0) {
    score = 0;
  }
  if (score > maxScore) {
    score = maxScore;
  }

  const factor = (score / maxScore) * 100;
  if (reverse) {
    if (factor < 20) {
      return "var(--score-green-strong)";
    } else if (factor >= 20 && factor < 40) {
      return "var(--score-green-light)";
    } else if (factor >= 40 && factor < 60) {
      return "var(--score-yellow)";
    } else if (factor >= 60 && factor < 80) {
      return "var(--score-orange)";
    } else if (factor >= 80 && factor <= 99) {
      return "var(--score-red-light)";
    } else if (factor > 99) {
      return "var(--score-red-strong)";
    }
  }

  if (factor < 20) {
    return "var(--score-red-strong)";
  } else if (factor >= 20 && factor < 40) {
    return "var(--score-red-light)";
  } else if (factor >= 40 && factor < 60) {
    return "var(--score-orange)";
  } else if (factor >= 60 && factor < 80) {
    return "var(--score-yellow)";
  } else if (factor >= 80 && factor <= 99) {
    return "var(--score-green-light)";
  } else if (factor > 99) {
    return "var(--score-green-strong)";
  }
}

// function interpolateColor(color1, color2, factor) {
//   const result = color1.slice();
//   for (let i = 0; i < 3; i++) {
//     result[i] = Math.round(result[i] + factor * (color2[i] - result[i]));
//   }
//   return result;
// }

// function rgbToHex(rgb) {
//   return (
//     "#" +
//     rgb
//       .map((value) => {
//         const hex = value.toString(16);
//         return hex.length === 1 ? "0" + hex : hex;
//       })
//       .join("")
//   );
// }

export const getTabLabel = (tag) => {
  return tag?.split(":")?.[1];
};

export function getRandomColor() {
  const letters = "0123456789ABCDEF";
  let color = "#";
  for (let i = 0; i < 6; i++) {
    color += letters[Math.floor(Math.random() * 16)];
  }
  return color;
}

export function getRandomId() {
  return Math.random().toString(36).substr(2, 9);
}

export const getScorePercentage = (s, decimalPlaces = 0) => {
  if (s <= 0) {
    s = 0;
  }
  const score = s * 10;
  return Number(score.toFixed(decimalPlaces));
};

export async function copyToClipboard(text) {
  try {
    // Serialize objects/arrays to JSON to avoid "[object Object]" from .toString()
    const value =
      typeof text === "object" && text !== null
        ? JSON.stringify(text, null, 2)
        : text;
    await navigator.clipboard.writeText(value);
  } catch (err) {
    logger.error("Failed to copy text: ", err);
  }
}

export const isMouseInBound = (e, rect) => {
  let ret = false;
  if (e.clientX >= rect.left && e.clientX <= rect.right) {
    ret = true;
  } else {
    ret = false;
  }
  return ret;
};

export function ctt(camelCaseStr) {
  if (typeof camelCaseStr !== "string") {
    return "";
  } // Ensure input is a string
  return camelCaseStr
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (str) => str.toUpperCase())
    .trim();
}

export const trackObject = (obj) => {
  return Object.entries(obj).reduce((acc, [key, value]) => {
    acc[ctt(key)] = value;
    return acc;
  }, {});
};

export const formatDashedToTitleCase = (text) => {
  // Split the input string into words
  const words = text.split("-");

  // Capitalize the first letter of each word
  const formattedWords = words.map(
    (word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase(),
  );

  // Join the words with a space
  return formattedWords.join(" ");
};

export function getColorFromSeed(seed) {
  // Split the seed into parts
  const parts = seed?.split("/");
  const datasetPart = parts[0];

  // Use a more sensitive hash function
  const hash = cyrb53(seed);

  // Define color ranges for different datasets
  const colorRanges = [
    { hueStart: 0, hueEnd: 60 }, // Reds to Yellows
    { hueStart: 120, hueEnd: 180 }, // Greens to Cyans
    { hueStart: 240, hueEnd: 300 }, // Blues to Purples
  ];

  // Select color range based on dataset part
  const rangeIndex = cyrb53(datasetPart) % colorRanges.length;
  const { hueStart, hueEnd } = colorRanges[rangeIndex];

  // Generate hue within the selected range
  const hueRange = hueEnd - hueStart;
  const hue = hueStart + (hash % hueRange);

  // Use hash to vary saturation and lightness
  const saturation = 70 + (hash % 20); // 70-90%
  const lightness = 45 + (hash % 15); // 45-60%

  // Convert HSL to RGB
  const color = hslToRgb(hue, saturation, lightness);

  return `#${color.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

// cyrb53 hash function (more sensitive to small changes)
function cyrb53(str, seed = 0) {
  let h1 = 0xdeadbeef ^ seed,
    h2 = 0x41c6ce57 ^ seed;
  for (let i = 0, ch; i < str.length; i++) {
    ch = str.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507);
  h1 ^= Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507);
  h2 ^= Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return 4294967296 * (2097151 & h2) + (h1 >>> 0);
}

// Helper function to convert HSL to RGB
function hslToRgb(h, s, l) {
  s /= 100;
  l /= 100;
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) =>
    l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [255 * f(0), 255 * f(8), 255 * f(4)].map(Math.round);
}

export function camelCaseToTitleCase(str) {
  // Handle empty or non-string input
  if (!str || typeof str !== "string") {
    return "";
  }

  // Add space before capital letters and capitalize first letter
  const withSpaces = str
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (str) => str.toUpperCase());

  return withSpaces.trim();
}

export function snakeCaseToTitleCase(text) {
  if (!text || typeof text !== "string") {
    return "";
  }

  return text
    .toLowerCase() // Handle UPPER_SNAKE_CASE
    .split("_") // Split by underscores
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1)) // Capitalize first letter
    .join(" "); // Join with spaces
}
export const isUUID = (value) =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );

export function stringAvatar(name) {
  if (!name || typeof name !== "string") {
    // Handle invalid input by returning default values
    return {
      sx: {
        bgcolor: "#cccccc", // Default background color
      },
      children: "?", // Default placeholder character
    };
  }

  const nameParts = name.trim().split(" "); // Split and trim the name
  const initials =
    nameParts.length >= 2
      ? `${nameParts[0][0]}${nameParts[1][0]}` // First letters of the first two words
      : `${nameParts[0][0]}`; // First letter of a single word

  return {
    sx: {
      bgcolor: stringToColor(name),
    },
    children: initials.toUpperCase(),
  };
}

export function stringToColor(string) {
  let hash = 0;
  for (let i = 0; i < string.length; i++) {
    hash = string.charCodeAt(i) + ((hash << 5) - hash);
  }
  let color = "#";
  for (let i = 0; i < 3; i++) {
    const value = (hash >> (i * 8)) & 0xff;
    color += `00${value.toString(16)}`.slice(-2);
  }
  return color;
}
export function formatEvalType(str) {
  // Handle empty or non-string input
  if (!str || typeof str !== "string") {
    return "";
  }

  // Split by underscore and convert to title case
  return str
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Custom hook to throttle a function call
 * @param {Function} callback - Function to be throttled
 * @param {number} delay - Throttle delay in milliseconds

 */

export const useThrottle = (callback, delay) => {
  const lastCallRef = useRef(0); // Tracks the last execution time
  const timeoutRef = useRef(null); // Timeout reference for scheduled calls
  const stoppedRef = useRef(false); // Flag to stop throttling

  // Throttled function
  const throttledFn = useCallback(
    (...args) => {
      if (stoppedRef.current) {
        return;
      } // Do nothing if throttling is stopped

      const now = Date.now();

      if (now - lastCallRef.current >= delay) {
        lastCallRef.current = now;
        callback(...args);
      } else {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = setTimeout(
          () => {
            lastCallRef.current = Date.now();
            callback(...args);
          },
          delay - (now - lastCallRef.current),
        );
      }
    },
    [callback, delay],
  );

  // Stop the throttle function
  const stop = useCallback(() => {
    clearTimeout(timeoutRef.current);
    stoppedRef.current = true;
  }, []);

  // Resume throttling if needed
  const resume = useCallback(() => {
    stoppedRef.current = false;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      stop();
    };
  }, [stop]);

  return [throttledFn, stop, resume];
};

// Converts a camelCase string to snake_case. Used by objectCamelToSnake for filter serialization.
export const camelToSnakeCase = (str, strict = false) => {
  if (!strict) {
    return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
  }

  return str
    .split("__")
    .map((part, index) =>
      index === 0
        ? part.replace(/([a-z])([A-Z])/g, "$1_$2").toLowerCase()
        : part
            .replace(/([a-z])([A-Z])/g, "$1_$2")
            .toLowerCase()
            .replace(/^([a-zA-Z])/, () => part[0]),
    )
    .join("__");
};

export const preventHeaderSelection = () => {
  const checkBox = document.querySelector(
    '[aria-label="Column with Header Selection"]',
  );
  checkBox?.addEventListener("click", function (e) {
    e.stopPropagation();
  });
};
export const getMarkColor = (weight, isOverlap = false, alpha = 0.5) => {
  let baseColor = "";

  if (weight > 0 && weight < 0.2) {
    baseColor = "#FA9B78";
  } else if (weight > 0.2 && weight < 0.4) {
    baseColor = "#fa8f7e";
  } else if (weight > 0.4 && weight < 0.6) {
    baseColor = "#f98881";
  } else if (weight > 0.6 && weight < 0.8) {
    baseColor = "#f78185";
  } else if (weight > 0.8 && weight < 1) {
    baseColor = "#F57B8A";
  } else {
    baseColor = "#F57B8A";
  }

  if (isOverlap) {
    return darkenColor(baseColor, 0.09, alpha);
  }

  return hexToRgba(baseColor, alpha);
};

export const darkenColor = (color, amount, alpha) => {
  const colorHex = parseInt(color.slice(1), 16);
  const r = (colorHex >> 16) & 0xff;
  const g = (colorHex >> 8) & 0xff;
  const b = colorHex & 0xff;

  const newR = Math.max(0, r - r * amount);
  const newG = Math.max(0, g - g * amount);
  const newB = Math.max(0, b - b * amount);

  return hexToRgba(
    `#${((1 << 24) | (newR << 16) | (newG << 8) | newB).toString(16).slice(1).toUpperCase()}`,
    alpha,
  );
};

// Converts hex color to RGBA format with a given alpha value
const hexToRgba = (hex, alpha) => {
  const colorHex = parseInt(hex.slice(1), 16);
  const r = (colorHex >> 16) & 0xff;
  const g = (colorHex >> 8) & 0xff;
  const b = colorHex & 0xff;

  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
};

export const formatISOCustom = (date) => {
  return formatISO(date).split("+")[0] + ".000Z";
};

// Start Generation Here
export const throttle = (func, limit) => {
  let inThrottle;
  return function (...args) {
    const context = this;
    if (!inThrottle) {
      func.apply(context, args);
      inThrottle = true;
      setTimeout(() => (inThrottle = false), limit);
    }
  };
};

export const getAveragePercentage = (a, b) => {
  if (typeof a !== "number" || typeof b !== "number") {
    throw new Error("Both a and b must be numbers");
  }

  if (b === 0) {
    throw new Error("Division by zero is not allowed");
  }

  return (a / b) * 100;
};
export function formatFileSize(bytes) {
  if (bytes === 0) {
    return "0 Bytes";
  }

  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
export const customizeAgGridPopup = (theme, targetText, iconPath) => {
  return (params) => {
    const ePopup = params.ePopup;
    ePopup.style.backgroundColor = theme.palette.background.paper;
    ePopup.style.borderRadius = "12px";
    ePopup.style.border = `1px solid ${theme.palette.divider}`;
    ePopup.style.width = "218px";
    ePopup.style.display = "flex";
    ePopup.style.flexDirection = "column";
    ePopup.style.fontFamily = "Inter, sans-serif";
    ePopup.style.color = theme.palette.text.primary;

    // Remove dividers
    ePopup
      .querySelectorAll(".ag-menu-separator")
      .forEach((divider) => divider.remove());

    const menuItems = ePopup.querySelectorAll(".ag-menu-option");
    menuItems.forEach((item) => {
      item.style.borderRadius = "4px";
      item.style.transition = "background-color 0.15s";
      item.addEventListener("mouseenter", () => {
        item.style.backgroundColor = theme.palette.action.hover;
      });
      item.addEventListener("mouseleave", () => {
        item.style.backgroundColor = "transparent";
      });
    });

    const elements = ePopup.querySelectorAll('span[data-ref="eName"]');

    elements.forEach((element) => {
      const itemName = element.innerText;

      // Basic styling
      element.style.fontWeight = 400;
      element.style.color = theme.palette.text.primary;
      element.style.fontSize = "14px";
      element.style.fontFamily = "Inter, sans-serif";
      element.style.paddingLeft = "14px";
      element.style.paddingTop = "14px";
      element.style.paddingBottom = "14px";

      const iconElement =
        element.parentElement.querySelector('[data-ref="eIcon"]');
      if (iconElement) {
        iconElement.style.color = theme.palette.text.disabled;
        iconElement.style.paddingLeft = "20px";

        if (itemName === targetText) {
          iconElement.innerHTML = "";

          const img = document.createElement("img");
          img.src = iconPath;
          img.style.width = "14px";
          img.style.height = "16px";

          iconElement.appendChild(img);
        }
      }
    });
  };
};

export const iconTypesMap = {
  // Document Types
  "application/pdf": "pdf",
  "application/msword": "doc",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
    "doc",
  "application/vnd.ms-excel": "xls",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xls",
  "application/rtf": "rtf",
  "text/rtf": "rtf",
  "text/plain": "txt",
  "application/json": "json",
  "text/csv": "csv",

  // Image Types
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/gif": "gif",
  "image/svg+xml": "svg",

  // Audio Types
  "audio/mpeg": "mp3",
  "audio/wav": "wav",

  // Video Types
  "video/mp4": "mp4",
  "video/avi": "avi",

  // Compressed Files
  "application/zip": "zip",
  "application/x-rar-compressed": "rar",
};

export function getFileExtension(mimeType) {
  const mappedType = iconTypesMap[mimeType];
  if (mappedType) {
    return mappedType;
  }

  const parts = mimeType?.split("/");
  return parts && parts.length > 1 ? parts[1] : "default";
}

export const transformSafeJson = (json) => {
  return json.replaceAll(`'`, `"`).replaceAll("None", " null ");
};

export function extractVariables(content, templateFormat = "mustache") {
  if (!content || content.length === 0) return [];

  const textContent = content.replace(/<[^>]*>/g, "");

  if (templateFormat === "jinja") {
    return extractJinjaVariables(textContent);
  }

  // Mustache mode: naive extraction (backward compatible)
  const variableMatches = textContent.match(/{{(.*?)}}/g);
  return variableMatches
    ? Array.from(
        new Set(variableMatches.map((v) => v.replace(/{{|}}/g, "").trim())),
      )
    : [];
}

export function sanitizeContent(content) {
  // Check if content consists only of newline characters
  if (/^\n+$/.test(content)) {
    const sanitized = content.replaceAll("\n", "");
    return sanitized;
  }
  return content;
}

export function mergeRefs(...refs) {
  return (node) => {
    refs.forEach((ref) => {
      if (typeof ref === "function") {
        ref(node);
      } else if (ref != null) {
        ref.current = node;
      }
    });
  };
}

// ---------------------------------------------------------------------------
// canonicalKeys / canonicalEntries / canonicalValues
//
// The axios response interceptor (`src/utils/axios.js`) adds camelCase
// aliases alongside every snake_case key in backend responses so existing
// camelCase reads keep working without a big-bang refactor. Those aliases
// are plain enumerable own-properties, so `Object.keys(obj)` returns BOTH
// the snake_case key AND its camelCase alias — the object effectively
// doubles in size when enumerated. Call sites that iterate API response
// objects (dynamic columns, metadata lists, chart categories, span
// attribute tables, etc) end up rendering every field twice, or merging
// duplicate entries into `flat[...]` buckets.
//
// Use these helpers instead of `Object.keys/entries/values` whenever you
// are iterating an API-originated object to build UI. Logic: keep a key
// if it contains an underscore (it's an original snake_case key) OR if
// converting it to snake_case yields a key that does NOT exist in the
// object (it's a genuine camelCase-only field). This drops only the
// alias half of every duplicated pair and leaves real camelCase keys
// alone.
// ---------------------------------------------------------------------------
const SNAKE_TO_CAMEL_ALIAS_RE = /_([a-z0-9])/g;

// Build the set of camelCase alias keys the axios interceptor would have
// added for snake_case keys in `obj`. Mirrors `snakeToCamelKey` in
// `src/utils/axios.js`. Forward-mapping this way is robust to digit
// separators (e.g. `tone_17_apr_2026` → `tone17Apr2026`), which a naïve
// reverse regex on camelCase cannot recover.
const buildAliasSet = (obj) => {
  const aliases = new Set();
  const keys = Object.keys(obj);
  for (let i = 0; i < keys.length; i += 1) {
    const k = keys[i];
    if (!k.includes("_")) continue;
    const alias = k.replace(SNAKE_TO_CAMEL_ALIAS_RE, (_, c) => c.toUpperCase());
    if (alias !== k) aliases.add(alias);
  }
  return aliases;
};

export const canonicalKeys = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  const aliases = buildAliasSet(obj);
  return Object.keys(obj).filter((key) => !aliases.has(key));
};

export const canonicalEntries = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  const aliases = buildAliasSet(obj);
  return Object.entries(obj).filter(([key]) => !aliases.has(key));
};

export const canonicalValues = (obj) => {
  if (!obj || typeof obj !== "object") return [];
  return canonicalKeys(obj).map((key) => obj[key]);
};

export const objectCamelToSnake = (obj) => {
  if (obj === null || obj === undefined) {
    return obj;
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => objectCamelToSnake(item));
  }

  if (typeof obj !== "object") {
    return obj;
  }

  return Object.keys(obj).reduce((acc, key) => {
    const snakeKey = camelToSnakeCase(key);
    acc[snakeKey] = objectCamelToSnake(obj[key]);
    return acc;
  }, {});
};

// Converts object keys from snake_case to camelCase
export const objectSnakeToCamel = (obj) => {
  if (obj === null || obj === undefined) return obj;
  if (Array.isArray(obj)) return obj.map(objectSnakeToCamel);
  if (typeof obj !== "object") return obj;
  return Object.keys(obj).reduce((acc, key) => {
    const camelKey = key.replace(/_([a-z])/g, (_, chr) => chr.toUpperCase());
    acc[camelKey] = objectSnakeToCamel(obj[key]);
    return acc;
  }, {});
};

export const paramsSerializer = () => {
  return {
    serialize: (params) => {
      const searchParams = new URLSearchParams();

      Object.entries(params).forEach(([key, value]) => {
        if (Array.isArray(value)) {
          value.forEach((v) => searchParams.append(key, v));
        } else if (value !== undefined) {
          searchParams.append(key, value);
        }
      });

      return searchParams.toString();
    },
  };
};

export const formatNumberSystem = (num, system = "american") => {
  const str = num.toString();
  const [intPart, decPart] = str.split(".");

  if (system === "indian") {
    let last3 = intPart.slice(-3);
    const rest = intPart.slice(0, -3);
    if (rest) last3 = "," + last3;
    const formatted = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",") + last3;
    return decPart && decPart !== "0" ? formatted + "." + decPart : formatted;
  }

  // fallback American system with regex
  const formatted = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return decPart && decPart !== "0" ? formatted + "." + decPart : formatted;
};

/**
 * Convert milliseconds into a readable, compact time string.
 * Examples:
 *   formatMs(1330.922769) -> "1.3s"
 *   formatMs(2000)        -> "2s"
 *   formatMs(60000)       -> "1m"
 *   formatMs(90000)       -> "1.5m"
 *   formatMs(3600000)     -> "1hr"
 *   formatMs(3661000)     -> "1hr 1m"
 *
 * @param {number} ms - time in milliseconds
 * @returns {string}
 */
export function formatMs(ms) {
  if (typeof ms !== "number" || isNaN(ms)) return "-";

  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(2)}m`;
  if (ms < 86_400_000) return `${(ms / 3_600_000).toFixed(2)}hr`;

  return `${(ms / 86_400_000).toFixed(1)}d`;
}

export const formatPercentage = (value) => {
  if (value == null || isNaN(value)) return "-";
  return value % 1 === 0 ? `${value}%` : `${value?.toFixed(2)}%`;
};

/**
 * Recursively flattens a nested object into a single-level object with dot notation keys.
 * Examples:
 *   flattenObject({ a: { b: { c: 1 } } }) -> { "a.b.c": 1 }
 *   flattenObject({ user: { name: "John", address: { city: "NYC" } } })
 *     -> { "user.name": "John", "user.address.city": "NYC" }
 *   flattenObject({ arr: [1, 2], obj: { x: 3 } })
 *     -> { "arr.0": 1, "arr.1": 2, "obj.x": 3 }
 *
 * @param {Object} obj - The object to flatten
 * @param {string} prefix - The prefix for keys (used internally for recursion)
 * @returns {Object} - The flattened object
 */
export const flattenObject = (obj, prefix = "") => {
  if (obj === null || obj === undefined) {
    return {};
  }

  const flattened = {};

  Object.keys(obj).forEach((key) => {
    const value = obj[key];
    const newKey = prefix ? `${prefix}.${key}` : key;

    if (
      value !== null &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      !(value instanceof Date)
    ) {
      // Recursively flatten nested objects
      Object.assign(flattened, flattenObject(value, newKey));
    } else if (Array.isArray(value)) {
      // Flatten arrays by index
      value.forEach((item, index) => {
        const arrayKey = `${newKey}.${index}`;
        if (
          item !== null &&
          typeof item === "object" &&
          !(item instanceof Date)
        ) {
          Object.assign(flattened, flattenObject(item, arrayKey));
        } else {
          flattened[arrayKey] = item;
        }
      });
    } else {
      // Base case: primitive value
      flattened[newKey] = value;
    }
  });

  return flattened;
};

export function jsonToDisplayString(obj) {
  if (obj === null) return "null";

  // primitives
  if (typeof obj !== "object") {
    return JSON.stringify(obj);
  }

  // arrays
  if (Array.isArray(obj)) {
    const items = obj.map((item) => jsonToDisplayString(item)).join(", ");
    return `[${items}]`;
  }

  // objects
  const entries = Object.entries(obj)
    .map(([key, value]) => `${key}: ${jsonToDisplayString(value)}`)
    .join(", ");

  return `{${entries}}`;
}

export function interpolateColorTokenBasedOnScore(
  score,
  maxScore = 10,
  reverse = false,
) {
  // Clamp
  score = Math.min(Math.max(score, 0), maxScore);

  // Reverse by flipping the score
  const normalizedScore = reverse ? maxScore - score : score;

  const factor = (normalizedScore / maxScore) * 100;

  // --- Shared color logic ---
  if (factor < 20) {
    return {
      bgcolor: palette("light").red.o5,
      color: palette("light").red[800],
    };
  } else if (factor < 40) {
    return {
      bgcolor: palette("light").red.o5,
      color: palette("light").red[800],
    };
  } else if (factor < 60) {
    return {
      bgcolor: palette("light").orange.o5,
      color: palette("light").orange[500] ?? "#A64B00",
    };
  } else if (factor < 80) {
    return {
      bgcolor: palette("light").yellow[50],
      color: palette("light").yellow[800],
    };
  } else if (factor < 99) {
    return {
      bgcolor: palette("light").green.o5,
      color: palette("light").green[800],
    };
  } else {
    return {
      bgcolor: palette("light").green.o10,
      color: palette("light").green[800],
    };
  }
}

export function deepEqual(x, y) {
  if (x === y) return true;
  if (typeof x !== typeof y) return false;
  if (x && y && typeof x === "object") {
    if (Array.isArray(x) && Array.isArray(y)) {
      if (x.length !== y.length) return false;
      return x.every((v, i) => deepEqual(v, y[i]));
    }
    const xKeys = Object.keys(x);
    const yKeys = Object.keys(y);
    if (xKeys.length !== yKeys.length) return false;
    return xKeys.every((key) => deepEqual(x[key], y[key]));
  }
  return false;
}

export const isJsonValue = (val) => {
  if (typeof val === "object" && val !== null) return true;

  if (typeof val !== "string") return false;

  try {
    const parsed = JSON.parse(val);
    return typeof parsed === "object" && parsed !== null;
  } catch {
    return false;
  }
};
export function safeParse(input) {
  if (typeof input !== "string") return input; // not a string → return as is

  try {
    return JSON.parse(input);
  } catch {
    return input; // invalid JSON string → return original
  }
}

export const formatStartTimeByRequiredFormat = (startTime, dateFormat) => {
  try {
    if (startTime == null) return null;
    const date = new Date(startTime);
    if (isNaN(date.getTime())) return null;
    return format(date, dateFormat || "dd MMM yyyy, hh:mm a");
  } catch {
    return null;
  }
};
/**
 * Normalizes recording data to a flat format.
 * Handles both nested format (from raw provider data) and flat format (from extraction).
 *
 * Backend can return recordings in two formats:
 * 1. Nested format (raw from provider): {stereoUrl, mono: {assistantUrl, customerUrl, combinedUrl}}
 * 2. Flat format (extracted): {stereo, assistant, customer, combined}
 *
 * @param {Object} recordings - Recording data in either format
 * @returns {Object} Normalized format {stereo, assistant, customer, combined, mono}
 *                   Note: mono is an alias for combined (for AudioDownloadButton compatibility)
 */
export const normalizeRecordings = (recordings) => {
  if (!recordings)
    return { stereo: "", assistant: "", customer: "", combined: "", mono: "" };

  // Check if it's nested format (has mono object or stereoUrl)
  const isNestedFormat = recordings.mono || recordings.stereoUrl;

  if (isNestedFormat) {
    const mono = recordings.mono || {};
    const combined = mono.combinedUrl || "";
    return {
      stereo: recordings.stereoUrl || "",
      assistant: mono.assistantUrl || "",
      customer: mono.customerUrl || "",
      combined,
      mono: combined, // alias for AudioDownloadButton
    };
  }

  // Already flat format
  const combined = recordings.combined || "";
  return {
    stereo: recordings.stereo || "",
    assistant: recordings.assistant || "",
    customer: recordings.customer || "",
    combined,
    mono: combined, // alias for AudioDownloadButton
  };
};

/*
 * Check if a string is valid JSON object or array.
 * @param {string} str - The string to check
 * @returns {boolean} True if the string is valid JSON object/array
 */
export function isValidJson(str) {
  if (typeof str !== "string") return false;
  try {
    const parsed = JSON.parse(str);
    return typeof parsed === "object" && parsed !== null;
  } catch {
    return false;
  }
}

/**
 * Response format types that indicate JSON output.
 */
const JSON_RESPONSE_FORMAT_TYPES = new Set(["json_object", "json", "object"]);

/**
 * Check if a response format indicates JSON output.
 * @param {string|object} responseFormat - The response format (string or object with type property)
 * @returns {boolean} True if the response format indicates JSON output
 */
export function isJsonResponseFormat(responseFormat) {
  if (!responseFormat) return false;

  const responseType =
    typeof responseFormat === "string" ? responseFormat : responseFormat?.type;

  if (!responseType) return false;
  return JSON_RESPONSE_FORMAT_TYPES.has(
    responseType.toLowerCase?.() || responseType,
  );
}

/**
 * Determine if output should be rendered as JSON based on response format and content.
 * @param {string|object} responseFormat - The response format setting
 * @param {string} content - The output content to check
 * @returns {boolean} True if output should be rendered as JSON
 */
export function shouldRenderAsJson(responseFormat, content) {
  // If explicitly JSON format, return true
  if (isJsonResponseFormat(responseFormat)) {
    return true;
  }
  // Auto-detect JSON in content
  return isValidJson(content);
}

/**
 * Check if output format indicates image output.
 * @param {string} outputFormat - The output format setting
 * @returns {boolean} True if the output format indicates image output
 */
export function isImageOutputFormat(outputFormat) {
  return outputFormat === "image";
}

/**
 * Check if a string is a valid HTTPS URL.
 * @param {string} str - The string to check
 * @returns {boolean} True if the string is a valid HTTPS URL
 */
export function isValidUrl(str) {
  if (typeof str !== "string" || !str) return false;
  try {
    const url = new URL(str);
    return url.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Check if a string is an image URL or base64 image data.
 * @param {string} str - The string to check
 * @returns {boolean} True if the string is an image URL or base64 image
 */
export function isImageContent(str) {
  if (typeof str !== "string" || !str) return false;

  // Check for base64 image data URL
  if (str.startsWith("data:image/")) {
    return true;
  }

  // Check for common image URL patterns
  const imageExtensions = /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico)(\?.*)?$/i;
  if (imageExtensions.test(str)) {
    return true;
  }

  // Check for URLs that likely serve images (common CDN patterns)
  const imageUrlPatterns = [
    /^https?:\/\/.*\/(images?|img|media|uploads?|assets?)\/.*$/i,
    /^https?:\/\/.*oaidalleapiprodscus\.blob\.core\.windows\.net/i, // DALL-E URLs
    /^https?:\/\/.*replicate\.delivery/i, // Replicate URLs
    /^https?:\/\/.*stability\.ai/i, // Stability AI URLs
  ];

  return imageUrlPatterns.some((pattern) => pattern.test(str));
}

/**
 * Determine if output should be rendered as an image.
 * @param {string} outputFormat - The output format setting
 * @param {string} content - The output content to check
 * @returns {boolean} True if output should be rendered as image
 */
export function shouldRenderAsImage(outputFormat, content) {
  // If explicitly image format, return true
  if (isImageOutputFormat(outputFormat)) {
    return true;
  }
  // Auto-detect image content
  return isImageContent(content);
}

// extraction for reasoning models
export function extractAllThoughts(str) {
  if (typeof str !== "string") return [];
  const matches = str.matchAll(/<thinking>([\s\S]*?)<\/thinking>/g);
  return Array.from(matches, (m) => m[1].trim());
}

// ---------------------------------------------------------------------------
// Fuzzy search primitives
//
// Reusable, pure helpers that back the Voice drawer's flat-match search.
// Callers split a query into tokens, then check each token against a leaf
// (or any string-bag) via `tokenMatchesLeaf`, which does an exact-substring
// pass first and only falls back to Levenshtein edit distance when the
// token is long enough for fuzzy matching to be safe.
//
// All functions operate on lowercased input — keep strings normalised
// before comparing. `tokenizeQuery` does that for queries.
// ---------------------------------------------------------------------------

// Splits a query into lowercased whitespace-separated tokens. Empty
// tokens are filtered out so trailing spaces don't produce no-op matches.
export const tokenizeQuery = (q) =>
  (q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);

// Levenshtein (edit) distance between two strings. Two-row rolling DP,
// O(m·n) time, O(min(m,n)) space. Used as the fuzzy fallback when a
// token has no exact substring hit — lets a typo'd `sation` still
// surface `station` (distance 1).
export const levenshtein = (a, b) => {
  if (a === b) return 0;
  const m = a.length;
  const n = b.length;
  if (m === 0) return n;
  if (n === 0) return m;
  let prev = new Array(n + 1);
  let curr = new Array(n + 1);
  for (let j = 0; j <= n; j += 1) prev[j] = j;
  for (let i = 1; i <= m; i += 1) {
    curr[0] = i;
    const ai = a.charCodeAt(i - 1);
    for (let j = 1; j <= n; j += 1) {
      const cost = ai === b.charCodeAt(j - 1) ? 0 : 1;
      const del = prev[j] + 1;
      const ins = curr[j - 1] + 1;
      const sub = prev[j - 1] + cost;
      let min = del < ins ? del : ins;
      if (sub < min) min = sub;
      curr[j] = min;
    }
    const tmp = prev;
    prev = curr;
    curr = tmp;
  }
  return prev[n];
};

// Max edit distance tolerated for a token of a given length. Short
// tokens (≤ 3 chars) are exact-only — a single edit on a 3-char word
// matches far too much (`the` → `toe`, `tie`, `tea`, …). Medium tokens
// (4-7) tolerate 1 edit, long tokens cap at 2.
export const fuzzyThreshold = (tok) => {
  const raw = Math.floor(tok.length / 4);
  return raw > 2 ? 2 : raw;
};

// Splits a lowercased string into its alphanumeric words — the unit
// Levenshtein compares against. `attributes.llm.model` → `["attributes",
// "llm", "model"]`, `"gpt-4"` → `["gpt", "4"]`. Callers pre-compute this
// per leaf so the fuzzy pass doesn't re-tokenise on every keystroke.
const WORD_SPLIT_RE = /[^a-z0-9]+/;
export const splitWords = (s) =>
  s ? s.split(WORD_SPLIT_RE).filter(Boolean) : [];

// Hybrid token → leaf match. Tries exact substring on the lowercased
// path and value first (cheap, zero false positives, preserves pre-
// fuzzy behaviour). Only falls back to Levenshtein against the pre-
// computed word list when the substring pass finds nothing AND the
// token is long enough for fuzzy matching to be safe.
export const tokenMatchesLeaf = (tok, pathLower, valueLower, words) => {
  if (pathLower.includes(tok) || valueLower.includes(tok)) return true;
  const thr = fuzzyThreshold(tok);
  if (thr === 0) return false;
  const ws = words || [];
  for (let i = 0; i < ws.length; i += 1) {
    const w = ws[i];
    if (Math.abs(w.length - tok.length) > thr) continue;
    if (levenshtein(tok, w) <= thr) return true;
  }
  return false;
};

// Strip the voice-detail wrapper (`observation_span.<n>.[span_attributes.]`)
// and bare `span_attributes.` so the saved mapping uses bare attribute paths.
export const stripAttributePathPrefix = (key) =>
  String(key ?? "")
    .replace(/^observation_span\.\d+\.(?:span_attributes\.)?/, "")
    .replace(/^span_attributes\./, "");
