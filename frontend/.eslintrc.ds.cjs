// Future AGI Design System guard.
// `yarn lint:ds` checks all of src. CI runs `yarn lint:ds:changed`, which checks
// for new restricted imports in changed files and style violations on added lines.
// Real exceptions carry `eslint-disable-next-line no-restricted-syntax -- ds: <reason>`
// with a reason from scripts/ds-optouts.mjs.

const DS = (selector, message) => ({ selector, message: `[DS] ${message}` });

// Match the component binding, not every binding in its declaration: a value
// import such as Button can share a declaration with a type-only Dialog.
const restrictDialog = (source, name) => {
  const from = `[source.value='${source}']`;
  const imports = `ImportDeclaration${from}[importKind!='type']`;
  const exports = `ExportNamedDeclaration${from}[exportKind!='type']`;
  const named = (field) =>
    `:matches([${field}.name='${name}'], [${field}.value='${name}'])`;
  const selectors = [
    `${imports} > ImportSpecifier[importKind!='type']${named("imported")}`,
    `${imports} > ImportNamespaceSpecifier`,
    `${exports} > ExportSpecifier[exportKind!='type']${named("local")}`,
    `ExportAllDeclaration${from}[exportKind!='type']`,
  ];
  if (name === "default") selectors.push(`${imports} > ImportDefaultSpecifier`);
  return DS(
    selectors.join(", "),
    `Use ConfirmDialog (confirm or delete), ModalWrapper (a form with submit) or ClosableDialog (any other popup), not a bare MUI Dialog from ${source}.`,
  );
};

const styleRestrictions = [
  DS(
    "ImportDeclaration[source.value='@mui/material'] > ImportSpecifier[imported.name=/^(Card|CardContent|CardHeader)$/]",
    "Use src/components/section (plain or panel) instead of Card.",
  ),
  DS(
    "Literal[value=/^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3}([0-9A-Fa-f]{2})?)?$/]",
    "Hex colour. Use a theme token (text.*, background.*, border.*, status.*).",
  ),
  DS(
    "Literal[value=/^(red|green|blue|orange|purple|pink|yellow|black|whiteScale)\\.(o?\\d+)$/]",
    "Legacy palette hue. Use status.<role>.* or text/border/background tokens.",
  ),
  // `raw` (not `value`) so bare numbers match too: fontSize: 14 and "14px".
  DS(
    "Property[key.name='fontSize'] > Literal[raw=/^[\"']?(8|9|10|17|18|19|21|22|28)(px)?[\"']?$/]",
    "Font size off the type scale (11 / 12 / 13 / 14 / 15 / 16 / 20 / 24 / 32). Use a typography variant.",
  ),
  DS(
    "JSXAttribute[name.name='fontSize'] Literal[raw=/^[\"']?(8|9|10|17|18|19|21|22|28)(px)?[\"']?$/]",
    "Font size off the type scale (11 / 12 / 13 / 14 / 15 / 16 / 20 / 24 / 32). Use a typography variant.",
  ),
  DS(
    "Property[key.name='fontWeight'] > Literal[raw=/^[\"']?(700|800|900|bold)[\"']?$/]",
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
    "JSXOpeningElement[name.name=/Tooltip$/] JSXAttribute[name.name=/^(slotProps|componentsProps|PopperProps)$/] Property[key.name=/^(bgcolor|backgroundColor|background|color|border|borderColor|boxShadow|borderRadius|fontSize|fontWeight|padding|p|px|py)$/]",
    "Tooltip restyle. Tooltips are the DS tooltip from the theme; only placement, width (maxWidth) and offset may change.",
  ),
  DS(
    "JSXOpeningElement[name.name=/^(Checkbox|Radio)$/] JSXAttribute[name.name=/^(icon|checkedIcon|indeterminateIcon)$/]",
    "Checkbox/Radio icon override. Use the theme's Checkbox or Radio as is.",
  ),
  DS(
    "JSXAttribute[name.name='icon'] Literal[value=/^(solar:(check-square|square|circle-dot)-(linear|bold)$|famicons:checkbox|mdi:checkbox|mdi:radiobox)/]",
    "Hand-drawn checkbox or radio icon. Render <Checkbox> or <Radio> (tabIndex={-1} inside a clickable row).",
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
    "Menu item override. Menu items are 12px regular from the theme.",
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
];

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
  // The plugins let existing eslint-disable comments resolve.
  plugins: ["react", "react-hooks", "react-refresh", "@typescript-eslint"],
  overrides: [
    {
      files: ["**/*.ts", "**/*.tsx"],
      parser: "@typescript-eslint/parser",
    },
    {
      // The shared dialogs are built on MUI Dialog. Style checks still apply.
      files: [
        "src/components/custom-dialog/**",
        "src/components/ModalWrapper/**",
      ],
      rules: {
        "no-restricted-imports": "off",
        "no-restricted-syntax": ["error", ...styleRestrictions],
      },
    },
  ],
  rules: {
    "react/prop-types": "off",
    "react-hooks/exhaustive-deps": "off",
    "react-refresh/only-export-components": "off",
    "no-restricted-imports": [
      "warn",
      {
        // Older components kept for the screens that already use them. A new
        // import of one prints a warning that names the replacement.
        patterns: [
          {
            group: ["**/EmptyLayout", "**/EmptyLayout/*"],
            message:
              "[DS] Use src/components/empty-state (EmptyState); EmptyLayout is kept only for existing screens.",
          },
          {
            group: ["**/custom-status-chip", "**/custom-status-chip/*"],
            message:
              "[DS] Use src/components/status-badge with src/utils/runStatus; CustomStatusChip is kept only for existing screens.",
          },
          {
            group: [
              "**/components/filter-panel",
              "**/filter-panel/FilterPanel",
            ],
            importNames: ["FilterPanel", "default"],
            message:
              "[DS] Use src/components/filter (FilterButton); this FilterPanel is kept only for its existing screens.",
          },
        ],
      },
    ],
    "no-restricted-syntax": [
      "error",
      ...styleRestrictions,
      restrictDialog("@mui/material", "Dialog"),
      restrictDialog("@mui/material/Dialog", "default"),
    ],
  },
};
