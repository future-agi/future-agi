#!/usr/bin/env python3
"""Build the design-system outputs from design-system/tokens/tokens.json.

Writes, next to tokens.json:
  tokens.css    CSS custom properties: light on :root / [data-theme="light"],
                dark on [data-theme="dark"] (and on :root:not([data-theme]) when the
                OS is dark), plus .ds-text-* type classes.
  tokens.js     window.DS = { tokens, light, dark, contrast } for the docs page.
And, outside this folder:
  frontend/src/theme/ds-tokens.js  ES module mapping the semantic tokens onto
                MUI v5 theme fields, read by the rest of frontend/src/theme.

It also checks contrast for every text / background pair the system promises
and exits non-zero if a required pair falls below its minimum.

Usage (from frontend/): yarn tokens:build
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "tokens"
THEME_TOKENS = Path(__file__).resolve().parents[2] / "src" / "theme" / "ds-tokens.js"
SRC = ROOT / "tokens.json"
THEMES = ("light", "dark")
REF = re.compile(r"\{([a-zA-Z0-9_.\-]+)\}")


def kebab(name):
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name).lower()


def load():
    return json.loads(SRC.read_text())


def primitive(tokens, path):
    node = tokens["primitive"]
    for part in path.split("."):
        node = node[part]
    return node


def color_leaves(node, prefix=()):
    """Yield (path tuple, {light, dark, use?}) for every themed colour token."""
    for key, value in node.items():
        if key.startswith("$"):
            continue
        if isinstance(value, dict) and "light" in value and "dark" in value:
            yield prefix + (key,), value
        elif isinstance(value, dict):
            yield from color_leaves(value, prefix + (key,))


def resolve(tokens, value, theme, semantic):
    """Replace {gray.50} with a primitive and {color.x.y} with a resolved semantic."""

    def sub(match):
        ref = match.group(1)
        if ref.startswith("color."):
            return semantic[theme][ref[len("color."):]]
        return primitive(tokens, ref)

    previous = None
    while previous != value:
        previous, value = value, REF.sub(sub, value)
    return value


def build_semantic(tokens):
    semantic = {t: {} for t in THEMES}
    leaves = list(color_leaves(tokens["color"]))
    # Two passes so semantic-to-semantic references resolve regardless of order.
    for _ in range(2):
        for path, leaf in leaves:
            name = ".".join(path)
            for theme in THEMES:
                semantic[theme][name] = resolve(tokens, leaf[theme], theme, semantic)
    shadows = {t: {} for t in THEMES}
    for name, leaf in tokens["shadow"].items():
        for theme in THEMES:
            shadows[theme][name] = resolve(tokens, leaf[theme], theme, semantic)
    return semantic, shadows


def css_var(name):
    return "--ds-" + "-".join(kebab(p) for p in name.split("."))


# ---------------------------------------------------------------- contrast

def rgba(value):
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.match(r"rgba?\(([^)]+)\)", value)
    parts = [p.strip() for p in m.group(1).split(",")]
    r, g, b = (float(p) for p in parts[:3])
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return (r, g, b, a)


def over(top, bottom):
    r1, g1, b1, a = rgba(top)
    r2, g2, b2, _ = rgba(bottom)
    mix = lambda x, y: round(x * a + y * (1 - a))
    return "#%02X%02X%02X" % (mix(r1, r2), mix(g1, g2), mix(b1, b2))


def luminance(hex_value):
    r, g, b, _ = rgba(hex_value)
    out = []
    for c in (r, g, b):
        c /= 255
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def ratio(fg, bg):
    a, b = luminance(fg), luminance(bg)
    return round((max(a, b) + 0.05) / (min(a, b) + 0.05), 2)


# (foreground, background, minimum, what) — backgrounds with alpha are composited over bg.surface.
PAIRS = [
    ("fg.default", "bg.surface", 4.5, "Primary text on panels"),
    ("fg.default", "bg.canvas", 4.5, "Primary text on the frame"),
    ("fg.secondary", "bg.surface", 4.5, "Secondary text"),
    ("fg.tertiary", "bg.surface", 4.5, "Meta text on panels"),
    ("fg.tertiary", "bg.canvas", 4.5, "Meta text on the frame"),
    ("fg.tertiary", "bg.subtle", 4.5, "Column headers"),
    ("fg.link", "bg.surface", 4.5, "Links"),
    ("fg.default", "bg.overlay", 4.5, "Menu text"),
    ("fg.inverse", "bg.inverse", 4.5, "Tooltip and toast text"),
    ("action.primaryFg", "action.primaryBg", 4.5, "Main button label"),
    ("action.dangerFg", "action.dangerBg", 4.5, "Destructive button label"),
    ("accent.onSolid", "accent.solid", 4.5, "Text on accent fill"),
    ("accent.fg", "accent.subtle", 4.5, "Accent text on accent tint"),
    ("icon.default", "bg.surface", 3.0, "Icons (non-text, 3:1)"),
    ("border.strong", "bg.surface", 1.4, "Input border visible"),
    ("border.focus", "bg.surface", 3.0, "Focus ring (non-text, 3:1)"),
    ("control.checked", "bg.surface", 3.0, "Checked control visible (non-text, 3:1)"),
    ("control.onChecked", "control.checked", 3.0, "Tick on checked control (non-text, 3:1)"),
    ("control.thumb", "control.track", 1.4, "Switch thumb on track (reference)"),
    ("fg.disabled", "bg.surface", 0, "Disabled text (exempt; listed for reference)"),
]
for role in ("success", "warning", "danger", "info", "neutral", "discovery"):
    PAIRS.append((f"status.{role}.fg", f"status.{role}.bg", 4.5, f"{role.title()} badge text"))
    PAIRS.append((f"status.{role}.onSolid", f"status.{role}.solid", 4.5, f"{role.title()} solid label"))


def contrast(semantic):
    rows, failures = [], []
    for fg, bg, minimum, what in PAIRS:
        row = {"fg": fg, "bg": bg, "min": minimum, "what": what}
        for theme in THEMES:
            surface = semantic[theme]["bg.surface"]
            back = over(semantic[theme][bg], surface)
            front = over(semantic[theme][fg], back)
            value = ratio(front, back)
            row[theme] = value
            if value < minimum:
                failures.append(f"{theme}: {fg} on {bg} = {value} (< {minimum})")
        rows.append(row)
    return rows, failures


# ---------------------------------------------------------------- writers

def write_css(tokens, semantic, shadows):
    def block(theme):
        lines = [f"  {css_var(n)}: {v};" for n, v in semantic[theme].items()]
        lines += [f"  --ds-shadow-{kebab(n)}: {v};" for n, v in shadows[theme].items()]
        return "\n".join(lines)

    static = []
    font = tokens["font"]
    static.append(f"  --ds-font-sans: {font['family']['sans']};")
    static.append(f"  --ds-font-mono: {font['family']['mono']};")
    for k, v in font["weight"].items():
        static.append(f"  --ds-weight-{k}: {v};")
    for k, v in tokens["space"].items():
        static.append(f"  --ds-space-{k}: {v}px;")
    for k, v in tokens["radius"].items():
        if not k.startswith("$"):
            static.append(f"  --ds-radius-{k}: {v}px;")
    for k, v in tokens["borderWidth"].items():
        if not k.startswith("$"):
            static.append(f"  --ds-border-width-{k}: {v}px;")
    for group, values in tokens["size"].items():
        for k, v in values.items():
            static.append(f"  --ds-size-{kebab(group)}-{kebab(k)}: {v}px;")
    for k, v in tokens["motion"]["duration"].items():
        static.append(f"  --ds-duration-{k}: {v}ms;")
    for k, v in tokens["motion"]["easing"].items():
        static.append(f"  --ds-easing-{k}: {v};")
    for k, v in tokens["z"].items():
        static.append(f"  --ds-z-{kebab(k)}: {v};")

    type_classes = []
    for name, role in font["role"].items():
        family = "var(--ds-font-mono)" if role.get("family") == "mono" else "var(--ds-font-sans)"
        decl = [
            f"font-family: {family}",
            f"font-size: {role['size']}px",
            f"line-height: {role['line']}px",
            f"font-weight: {role['weight']}",
            f"letter-spacing: {role['tracking']}",
        ]
        if role.get("case") == "uppercase":
            decl.append("text-transform: uppercase")
        if role.get("numeric") == "tabular":
            decl.append("font-variant-numeric: tabular-nums")
        type_classes.append(f".ds-text-{name} {{ {'; '.join(decl)}; }}")

    css = f"""/* Future AGI Design System tokens v{tokens['$meta']['version']}.
   Generated from frontend/design-system/tokens/tokens.json by `yarn tokens:build`.
   Do not edit by hand: change tokens.json and rebuild. */

:root,
[data-theme="light"] {{
{chr(10).join(static)}
{block('light')}
  color-scheme: light;
}}

[data-theme="dark"] {{
{block('dark')}
  color-scheme: dark;
}}

@media (prefers-color-scheme: dark) {{
  :root:not([data-theme]) {{
{block('dark')}
    color-scheme: dark;
  }}
}}

{chr(10).join(type_classes)}
"""
    (ROOT / "tokens.css").write_text(css)


def write_js(tokens, semantic, shadows, rows):
    payload = {
        "tokens": tokens,
        "light": {**semantic["light"], **{f"shadow.{k}": v for k, v in shadows["light"].items()}},
        "dark": {**semantic["dark"], **{f"shadow.{k}": v for k, v in shadows["dark"].items()}},
        "contrast": rows,
    }
    (ROOT / "tokens.js").write_text(
        "/* Generated by `yarn tokens:build` (frontend/design-system). Do not edit. */\n"
        f"window.DS = {json.dumps(payload, indent=1)};\n"
    )


def write_mui(tokens, semantic, shadows):
    def palette(theme):
        s = semantic[theme]
        def status(role):
            return {
                "lighter": s[f"status.{role}.bg"], "light": s[f"status.{role}.border"],
                "main": s[f"status.{role}.solid"], "dark": s[f"status.{role}.fg"],
                "darker": s[f"status.{role}.fg"], "contrastText": s[f"status.{role}.onSolid"],
            }
        return {
            "mode": theme,
            "primary": {
                "lighter": s["accent.subtle"], "light": s["accent.border"],
                "main": s["action.primaryBg"], "dark": s["action.primaryBgHover"],
                "darker": s["fg.link"], "contrastText": s["action.primaryFg"],
            },
            "secondary": {
                "lighter": s["accent.subtle"], "light": s["accent.border"],
                "main": s["accent.solid"], "dark": s["accent.solidHover"],
                "darker": s["accent.fg"], "contrastText": s["accent.onSolid"],
            },
            "success": status("success"), "warning": status("warning"),
            "error": status("danger"), "info": status("info"),
            "grey": {k: v for k, v in tokens["primitive"]["gray"].items()},
            "text": {
                "primary": s["fg.default"], "secondary": s["fg.secondary"],
                "tertiary": s["fg.tertiary"], "subtitle": s["fg.tertiary"],
                "muted": s["fg.secondary"], "disabled": s["fg.disabled"],
                "link": s["fg.link"],
            },
            "background": {
                "default": s["bg.canvas"], "paper": s["bg.surface"],
                "neutral": s["bg.subtle"], "subtle": s["bg.subtle"],
                "accent": s["bg.muted"], "muted": s["bg.muted"],
                "overlay": s["bg.overlay"], "inverse": s["bg.inverse"],
            },
            "divider": s["border.default"],
            "border": {
                "subtle": s["border.subtle"], "default": s["border.default"],
                "hover": s["border.strong"], "strong": s["border.strong"],
                "focus": s["border.focus"], "selected": s["border.selected"],
            },
            "action": {
                "active": s["icon.default"], "hover": s["bg.hover"],
                "selected": s["bg.selected"], "focus": s["bg.pressed"],
                "disabled": s["fg.disabled"], "disabledBackground": s["bg.muted"],
                "hoverOpacity": 0.04, "selectedOpacity": 0.06, "disabledOpacity": 0.48,
            },
            "status": {role: {k: s[f"status.{role}.{k}"] for k in ("fg", "bg", "border", "solid", "onSolid")}
                       for role in ("success", "warning", "danger", "info", "neutral", "discovery")},
            "data": [s[f"data.{i}"] for i in range(1, 9)],
            "dataMono": [s[f"dataMono.{i}"] for i in range(1, 9)],
            "control": {k: s[f"control.{k}"] for k in ("checked", "onChecked", "track", "thumb")},
        }

    roles = tokens["font"]["role"]

    def variant(name):
        r = roles[name]
        out = {"fontSize": f"{r['size'] / 16}rem", "lineHeight": f"{r['line']}px", "fontWeight": r["weight"]}
        if r["tracking"] != "0":
            out["letterSpacing"] = r["tracking"]
        if r.get("case") == "uppercase":
            out["textTransform"] = "uppercase"
        if r.get("family") == "mono":
            out["fontFamily"] = tokens["font"]["family"]["mono"]
        return out

    typography = {
        "fontFamily": tokens["font"]["family"]["sans"],
        "fontWeightRegular": 400, "fontWeightMedium": 500,
        "fontWeightSemiBold": 600, "fontWeightBold": 600,
        "h1": variant("title-lg"), "h2": variant("title-md"),
        "h3": variant("title-sm"), "h4": variant("title-sm"),
        "h5": variant("title-xs"), "h6": variant("title-xs"),
        "subtitle1": {**variant("label-md"), "fontSize": "0.875rem"},
        "subtitle2": variant("label-md"),
        "body1": variant("body-md"), "body2": variant("body-sm"),
        "caption": variant("caption"), "overline": variant("overline"),
        "button": {**variant("label-md"), "textTransform": "none"},
    }
    for name in roles:
        typography[name.replace("-", "_")] = variant(name)
    # The product's own variants (s1 … l3): size and line height of a role, so
    # a screen built on them keeps its sizes when this theme is applied.
    for name, role in tokens["font"].get("productAlias", {}).items():
        if name.startswith("$"):
            continue
        v = variant(role)
        typography[name] = {"fontSize": v["fontSize"], "lineHeight": v["lineHeight"]}
        if name == "subheader":
            typography[name].update(fontWeight=v["fontWeight"], letterSpacing=v.get("letterSpacing", "0"))

    module = f"""/* Future AGI Design System v{tokens['$meta']['version']} — MUI v5 mapping.
   Generated from frontend/design-system/tokens/tokens.json by `yarn tokens:build`.
   Do not edit by hand: change tokens.json and rebuild.

   Drop-in plan for frontend/src/theme:
     palette(mode)      -> dsPalette(mode)
     typography         -> dsTypography
     shape.borderRadius -> dsShape.borderRadius (6)
     customShadows      -> dsCustomShadows(mode)
   Every new typography role is also exposed as a variant, e.g. <Typography variant="body_sm">,
   and so are the product's own variants (s1 … l3, subheader) — a theme that drops them
   makes that text fall back to 14–16px. Component styling is not in this file: it
   lives in frontend/src/theme/overrides/components/. */

const palettes = {json.dumps({t: palette(t) for t in THEMES}, indent=2)};

export function dsPalette(mode) {{
  return palettes[mode === "dark" ? "dark" : "light"];
}}

export const dsTypography = {json.dumps(typography, indent=2)};

export const dsShape = {{ borderRadius: {tokens['radius']['md']} }};

export const dsRadius = {json.dumps({k: v for k, v in tokens['radius'].items() if not k.startswith('$')})};

export const dsSize = {json.dumps(tokens['size'], indent=2)};

const shadows = {json.dumps(shadows, indent=2)};

export function dsCustomShadows(mode) {{
  const s = shadows[mode === "dark" ? "dark" : "light"];
  return {{
    z1: s.raised, z4: s.overlay, z8: s.overlay, z12: s.overlay, z16: s.modal, z20: s.modal, z24: s.modal,
    card: "none", dropdown: s.overlay, dialog: s.modal, drawer: s.drawer,
    focus: s.focus, focusInput: s.focusInput,
    primary: "none", secondary: "none", info: "none", success: "none", warning: "none", error: "none",
  }};
}}

export const dsMotion = {json.dumps(tokens['motion'], indent=2)};
"""
    THEME_TOKENS.write_text(module)


def main():
    tokens = load()
    semantic, shadows = build_semantic(tokens)
    rows, failures = contrast(semantic)
    write_css(tokens, semantic, shadows)
    write_js(tokens, semantic, shadows, rows)
    write_mui(tokens, semantic, shadows)
    print(f"Wrote tokens.css, tokens.js, src/theme/ds-tokens.js ({len(semantic['light'])} colour tokens per theme)")
    width = max(len(r["what"]) for r in rows)
    for r in rows:
        mark = "" if r["min"] == 0 or (r["light"] >= r["min"] and r["dark"] >= r["min"]) else "  <-- below minimum"
        print(f"  {r['what']:<{width}}  light {r['light']:>5}  dark {r['dark']:>5}{mark}")
    if failures:
        print("\nContrast failures:\n  " + "\n  ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
