// Future AGI Design System guard (product-design/design-system).
// Run with `npm run lint:ds`. Each rule flags styling that should come from the
// theme or a shared component instead of being written by hand on a screen.
// Errors: a screen that restyles what the theme owns fails lint. Real exceptions
// (data colours, overlay shadows) carry an eslint-disable comment with a reason.

const DS = (selector, message) => ({ selector, message: `[DS] ${message}` });

module.exports = {
  root: true,
  env: { browser: true, es2021: true },
  parserOptions: {
    ecmaVersion: "latest",
    sourceType: "module",
    ecmaFeatures: { jsx: true },
  },
  ignorePatterns: [
    "src/theme/**",
    "src/**/_mock/**",
    "src/**/__tests__/**",
    "src/**/*.test.*",
    "src/**/*.stories.*",
    "src/assets/**",
  ],
  // Loaded only so existing eslint-disable comments for these rules resolve;
  // the rules themselves stay off here (the main config runs them).
  plugins: ["react", "react-hooks", "react-refresh"],
  rules: {
    "react/prop-types": "off",
    "react-hooks/exhaustive-deps": "off",
    "react-refresh/only-export-components": "off",
    "no-restricted-imports": [
      "error",
      {
        paths: [
          {
            name: "@mui/material",
            importNames: ["Card", "CardContent", "CardHeader"],
            message: "[DS] Use src/components/section (plain or panel) instead of Card.",
          },
        ],
      },
    ],
    "no-restricted-syntax": [
      "error",
      DS(
        "Literal[value=/^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3}([0-9A-Fa-f]{2})?)?$/]",
        "Hex colour. Use a theme token (text.*, background.*, border.*, status.*).",
      ),
      DS(
        "Literal[value=/^(red|green|blue|orange|purple|pink|yellow|black|whiteScale)\\.(o?\\d+)$/]",
        "Legacy palette hue. Use status.<role>.* or text/border/background tokens.",
      ),
      DS(
        "Property[key.name='fontSize'] > Literal[value=/^(8|9|10|11|14|15|17|18|19|21|22|28|32)(px)?$/]",
        "Font size off the UI scale (12 / 13 / 16 / 20 / 24). Use a typography variant.",
      ),
      DS(
        "JSXAttribute[name.name='fontSize'] Literal[value=/^(8|9|10|11|14|15|17|18|19|21|22|28|32)(px)?$/]",
        "Font size off the UI scale (12 / 13 / 16 / 20 / 24). Use a typography variant.",
      ),
      DS(
        "Property[key.name='fontWeight'] > Literal[value=/^(700|800|900|bold)$/]",
        "Weight above 600. The DS uses 400 / 500 / 600.",
      ),
      DS(
        "Property[key.name='textTransform'] > Literal[value='uppercase']",
        "Uppercase label. The DS uses sentence case.",
      ),
      DS(
        "JSXText[value=/^\\s*[A-Z]{3,}(\\s+[A-Z]{2,})+\\s*$/]",
        "Typed-in capitals. Write labels in sentence case.",
      ),
      DS(
        "Property[key.value='&.Mui-selected'] Property[key.name='color'] > Literal[value='primary.main']",
        "Purple selected state. Selected tabs, items and rows use text.primary; purple is for the main action only.",
      ),
      DS(
        "JSXOpeningElement[name.name=/^(Avatar|AvatarGroup)$/] JSXAttribute[name.name='sx'] Property[key.name=/^(bgcolor|backgroundColor|color|border|borderColor)$/]",
        "Avatar colour override. Avatars are the neutral DS avatar; only size may change.",
      ),
      DS(
        "JSXOpeningElement[name.name=/^(Tabs|Tab|CustomTabs|SegmentedTabs|ToggleButtonGroup|ToggleButton)$/] JSXAttribute[name.name='sx'] Property[key.name=/^(height|minHeight|fontSize|fontWeight|color|bgcolor|backgroundColor|borderRadius|border|textTransform|padding|px|py)$/]",
        "Tab / segmented-control override. The theme styles these; don't restyle per screen.",
      ),
      DS(
        "JSXOpeningElement[name.name=/^(MenuItem|ListItemText)$/] JSXAttribute[name.name='sx'] Property[key.name=/^(fontSize|fontWeight|color|minHeight|height)$/]",
        "Menu item override. Menu items are 13px regular from the theme.",
      ),
      DS(
        "JSXOpeningElement[name.name='Alert'] JSXAttribute[name.name='sx'] Property[key.name=/^(bgcolor|backgroundColor|color|border|borderColor|borderRadius)$/]",
        "Alert colour override. Use severity; the theme colours it.",
      ),
      DS(
        "JSXOpeningElement[name.name=/^(Card|Paper)$/] JSXAttribute[name.name='sx'] Property[key.name=/^(boxShadow|borderRadius|bgcolor|backgroundColor)$/]",
        "Card/Paper styling. Use the shared Section (plain or panel).",
      ),
      DS(
        "Property[key.name='boxShadow'] > Literal[value!='none']",
        "Custom shadow. In-page surfaces have no shadow; overlays get theme shadows.",
      ),
      DS(
        "JSXOpeningElement[name.name=/^(Button|LoadingButton|IconButton|Chip)$/] > JSXAttribute[name.name='sx'] > JSXExpressionContainer > ObjectExpression > Property[key.name=/^(height|minHeight|fontSize|fontWeight|borderRadius|textTransform)$/]",
        "Button/Chip size or type override. Use the component's size prop; the theme sets the rest.",
      ),
    ],
  },
};
