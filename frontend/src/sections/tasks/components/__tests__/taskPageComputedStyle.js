import { rgb } from "./samplingContrastTestUtils";

// JSDOM applies stylesheet order but not selector specificity to color. This
// matters here: MUI's shrink/error selector wins over the old helper's .Mui-error
// rule in Chromium, while raw JSDOM reports the opposite. Resolve the CSSOM
// cascade for ink, backgrounds and field focus, retaining native layout styles.
// No specimen colors or production values are injected into the rendered DOM.
function selectors(text) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    if ("([".includes(text[i])) depth++;
    if (")]".includes(text[i])) depth--;
    if (text[i] === "," && depth === 0) {
      parts.push(text.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(text.slice(start));
  return parts;
}
export function specificity(selector) {
  let extra = 0;
  let text = selector;
  const functional = /:(is|not|has|where)\(/;
  for (let match; (match = functional.exec(text)); ) {
    let depth = 1;
    let end = match.index + match[0].length;
    const start = end;
    while (depth && end < text.length) {
      if (text[end] === "(") depth++;
      if (text[end] === ")") depth--;
      end++;
    }
    if (match[1] !== "where")
      extra += Math.max(
        ...selectors(text.slice(start, end - 1)).map(specificity),
      );
    text = text.slice(0, match.index) + text.slice(end);
  }
  const ids = (text.match(/#[\w-]+/g) || []).length;
  const classes = (
    text.match(/\.[\w-]+|\[[^\]]+\]|(?<!:):(?!:)[\w-]+(?:\([^)]*\))?/g) || []
  ).length;
  const types = (
    text
      .replace(
        /#[\w-]+|\.[\w-]+|\[[^\]]+\]|(?<!:):(?!:)[\w-]+(?:\([^)]*\))?/g,
        "",
      )
      .match(/[a-zA-Z][\w-]*/g) || []
  ).length;
  return extra + ids * 1000000 + classes * 1000 + types;
}
export function pageComputedStyle() {
  const ink = new Map();
  const focusProperties = {
    outline: "outline",
    "outline-color": "outlineColor",
    "outline-offset": "outlineOffset",
    "box-shadow": "boxShadow",
    "background-color": "backgroundColor",
  };
  const focusRules = Object.fromEntries(
    Object.values(focusProperties).map((property) => [property, new Map()]),
  );
  const textFill = new Map();
  const placeholderInk = new Map();
  const placeholderFill = new Map();
  const placeholderOpacity = new Map();
  const apply = (map, element, rank, value) => {
    if (!map.has(element) || rank >= map.get(element).rank)
      map.set(element, { rank, value });
  };
  const collect = (list) => {
    for (const rule of list) {
      if (rule.selectorText) {
        for (const rawSelector of selectors(rule.selectorText)) {
          const selector = rawSelector.trim();
          const placeholder = selector.endsWith("::placeholder");
          // Other pseudo elements do not paint the element's own text.
          if (
            (selector.includes("::") && !placeholder) ||
            /:-[a-z]+-/.test(selector) ||
            // JSDOM matches :autofill even on untouched inputs. These tests
            // have no browser autofill state; do not invent its important ink.
            selector.includes(":autofill")
          )
            continue;
          const properties = placeholder
            ? ["color", "opacity", "-webkit-text-fill-color"]
            : [
                "color",
                "-webkit-text-fill-color",
                ...Object.keys(focusProperties),
              ];
          if (
            !properties.some((property) =>
              rule.style.getPropertyValue(property),
            )
          )
            continue;
          // Match each selector once, not every rule against every text node.
          // MUI's generated class selectors make this substantially cheaper.
          const matches = document.querySelectorAll(
            placeholder ? selector.replace(/::placeholder$/, "") : selector,
          );
          for (const property of properties) {
            const value = rule.style.getPropertyValue(property);
            if (!value) continue;
            const rank =
              specificity(selector) +
              (rule.style.getPropertyPriority(property) ? 1000000000 : 0);
            const map = placeholder
              ? property === "color"
                ? placeholderInk
                : property === "opacity"
                  ? placeholderOpacity
                  : placeholderFill
              : property === "color"
                ? ink
                : property === "-webkit-text-fill-color"
                  ? textFill
                  : focusRules[focusProperties[property]];
            for (const element of matches) apply(map, element, rank, value);
          }
        }
      }
      if (
        rule.cssRules &&
        (!rule.conditionText || window.matchMedia(rule.conditionText).matches)
      )
        collect(rule.cssRules);
    }
  };
  for (const sheet of document.styleSheets) collect(sheet.cssRules);
  const cache = new Map();
  // Preserve currentColor as an inherited keyword until the glyph's own color
  // is known (including ::placeholder), not its ancestor's resolved RGB value.
  const fillTokens = new Map();
  const cascaded = (map, element, property) => {
    let winner = map.get(element);
    const value = element.style.getPropertyValue(property);
    if (value) {
      const rank =
        100000000 +
        (element.style.getPropertyPriority(property) ? 1000000000 : 0);
      if (!winner || rank > winner.rank) winner = { rank, value };
    }
    return winner?.value;
  };
  const normalizeColor = (value) => {
    const declaration = document.createElement("span").style;
    declaration.color = value;
    return declaration.color;
  };
  const resolveFill = (value, color) =>
    normalizeColor(value.toLowerCase() === "currentcolor" ? color : value);
  const get = (element, pseudo = null) => {
    if (pseudo === "::placeholder") {
      const normal = get(element);
      const declaration = placeholderInk.get(element)?.value;
      const color = normalizeColor(
        declaration &&
          !["inherit", "currentcolor", "unset"].includes(
            declaration.toLowerCase(),
          )
          ? declaration
          : normal.color,
      );
      const fill = placeholderFill.get(element)?.value;
      const fillToken =
        !fill || ["inherit", "unset"].includes(fill.toLowerCase())
          ? fillTokens.get(element)
          : fill.toLowerCase() === "initial"
            ? "currentColor"
            : fill;
      const opacity = Number(placeholderOpacity.get(element)?.value ?? 1);
      // Placeholder opacity affects only its glyphs, not the input surface.
      const withOpacity = (inkColor) => {
        const channels = rgb(inkColor);
        return `rgba(${channels.slice(0, 3).join(", ")}, ${(channels[3] ?? 1) * opacity})`;
      };
      const inkColor = withOpacity(color);
      const fillColor = withOpacity(resolveFill(fillToken, color));
      return new Proxy(normal, {
        get: (target, key) => {
          if (key === "color") return inkColor;
          if (["webkitTextFillColor", "WebkitTextFillColor"].includes(key))
            return fillColor;
          if (key === "getPropertyValue")
            return (property) =>
              property === "-webkit-text-fill-color"
                ? fillColor
                : property === "color"
                  ? inkColor
                  : normal.getPropertyValue(property);
          return Reflect.get(target, key);
        },
      });
    }
    if (cache.has(element)) return cache.get(element);
    const native = getComputedStyle(element);
    let color = cascaded(ink, element, "color");
    if (
      !color ||
      ["inherit", "currentcolor", "unset"].includes(color.toLowerCase())
    )
      color = element.parentElement
        ? get(element.parentElement).color
        : native.color;
    const declaration = document.createElement("span").style;
    declaration.color = color;
    color = declaration.color || native.color;
    let fillToken = cascaded(textFill, element, "-webkit-text-fill-color");
    if (!fillToken || ["inherit", "unset"].includes(fillToken.toLowerCase())) {
      if (element.parentElement) get(element.parentElement);
      fillToken = fillTokens.get(element.parentElement) || "currentColor";
    }
    if (fillToken.toLowerCase() === "initial") fillToken = "currentColor";
    fillTokens.set(element, fillToken);
    const fillColor = resolveFill(fillToken, color);
    const resolved = {
      color,
      webkitTextFillColor: fillColor,
      WebkitTextFillColor: fillColor,
    };
    for (const [cssName, key] of Object.entries(focusProperties)) {
      let rule = focusRules[key].get(element);
      const inlineValue = element.style.getPropertyValue(cssName);
      if (inlineValue) {
        const rank =
          100000000 +
          (element.style.getPropertyPriority(cssName) ? 1000000000 : 0);
        if (!rule || rank > rule.rank) rule = { rank, value: inlineValue };
      }
      if (!rule) continue;
      let value = rule.value;
      if (value.toLowerCase() === "currentcolor") value = color;
      if (value === "inherit" && element.parentElement)
        value = get(element.parentElement)[key];
      const normalized = document.createElement("span").style;
      normalized.setProperty(cssName, value);
      resolved[key] = normalized[key] || value;
    }
    const result = new Proxy(native, {
      get: (target, key) => {
        if (key === "getPropertyValue")
          return (property) =>
            property === "-webkit-text-fill-color"
              ? fillColor
              : property === "color"
                ? color
                : native.getPropertyValue(property);
        return key in resolved ? resolved[key] : Reflect.get(target, key);
      },
    });
    cache.set(element, result);
    return result;
  };
  return get;
}
