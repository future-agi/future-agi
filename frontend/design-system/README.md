# Future AGI Design System

The source of the product's design tokens and the design-system docs. The theme in
`frontend/src/theme/` is built from these tokens.

## Files

| Path | What |
|---|---|
| `tokens/tokens.json` | **Source of truth.** Primitives, semantic colour tokens with light and dark values, type roles, spacing, radius, sizes, shadows, motion |
| `tools/build_tokens.py` | Builds the generated files below and checks text contrast in both themes |
| `tokens/tokens.css` | Generated CSS custom properties (`--ds-*`) for the docs |
| `tokens/tokens.js` | Generated bundle for the docs page |
| `../src/theme/ds-tokens.js` | Generated MUI mapping (`dsPalette(mode)`, `dsTypography`, `dsShape`, `dsCustomShadows(mode)`, `dsSize`) that the theme reads |
| `index.html`, `docs.css`, `docs.js`, `ds.css`, `assets/` | The docs site. Every demo renders in light and dark |

Component styling isn't generated: it lives in `frontend/src/theme/overrides/components/`.

## Changing a token

1. Edit `tokens/tokens.json`.
2. From `frontend/`, run `yarn tokens:build`. It rewrites `tokens.css`, `tokens.js` and `src/theme/ds-tokens.js`. The build fails and names the pair if a promised text/background contrast drops below its minimum.
3. Bump the `?v=` on the asset links in `index.html` so the hosted docs serve the new files.

## Viewing the docs

From `frontend/design-system/`, run `python3 -m http.server 8000` and open `http://localhost:8000/`.

## Building UI with it

Follow the `building-ui` skill (`.agents/skills/building-ui/SKILL.md`). Before opening a PR, run `yarn lint:ds:changed && yarn ds:optouts` from `frontend/`; CI runs both.
