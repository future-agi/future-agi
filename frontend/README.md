# Frontend

The Future AGI web app — a React 18 + Vite SPA that talks to the Django backend in [`futureagi/`](../futureagi/).

## Setup

### Initial Setup

```bash
# From the repo root, so husky hooks are installed too
yarn install
```

The dev server runs on **port 3031**. The backend must be running separately — see [`../futureagi/README.md`](../futureagi/README.md), or run `docker compose up -d` from the repo root for the full stack.

### Tech Stack

- ✅ **React 18** + **Vite** (JavaScript / JSX)
- ✅ **MUI v5** + AG Grid + MUI X Data Grid for UI
- ✅ **TanStack Query** for server state, **Zustand** for client state
- ✅ **React Router v6** for routing
- ✅ **React Hook Form** + **Yup** / **Zod** for forms
- ✅ **Vitest** + **React Testing Library** for tests
- ✅ **ESLint** (Airbnb) + **Prettier** for lint/format
- ✅ **Storybook** for component development

### Pre-commit Hooks

**This project uses pre-commit hooks to ensure code quality.** They run automatically before each commit on staged `frontend/src/**` files.

#### What do they check?

- ✅ Linting (ESLint — Airbnb config)
- ✅ Formatting (Prettier)
- ✅ Import order + unused imports

#### First-time setup

```bash
# Install hooks (one-time, from repo root)
yarn install
```

Husky + lint-staged pick up automatically — no extra step needed.

#### Daily usage

Hooks run automatically on `git commit`. You can also run manually:

```bash
yarn lint                # Run ESLint
yarn lint:fix            # ESLint with auto-fix
yarn prettier            # Format with Prettier
yarn type-check          # No-op today; runs `tsc --noEmit` only if tsconfig.json is added
```

### Common Commands

```bash
# Dev
yarn dev                 # Start dev server → http://localhost:3031
yarn dev:host            # Dev server exposed on local network
yarn build               # Production build
yarn start               # Preview production build

# Code Quality
yarn lint                # Run ESLint
yarn lint:fix            # ESLint with auto-fix
yarn prettier            # Format with Prettier
yarn type-check          # No-op unless tsconfig.json is added

# Testing
yarn test                # Watch mode
yarn test:run            # Run once
yarn test:ui             # Browser-based test runner
yarn test:coverage       # With coverage report
yarn test:changed        # Run tests for changed files only

# Storybook
yarn storybook           # → http://localhost:6006
yarn build-storybook
```

### Project Layout

```
frontend/
├── src/
│   ├── api/             # API clients (axios)
│   ├── auth/            # Auth context, guards, login flows
│   ├── components/      # Reusable UI components
│   ├── contexts/        # React contexts
│   ├── hooks/           # Custom hooks
│   ├── layouts/         # Page layouts (dashboard, auth, etc.)
│   ├── locales/         # i18n
│   ├── pages/           # Route-level components
│   ├── routes/          # Router config + path constants
│   ├── sections/        # Feature-scoped compositions
│   ├── theme/           # MUI theme overrides
│   ├── utils/           # Pure helpers, test-utils
│   ├── _mock/           # Mock data
│   ├── __tests__/       # Cross-cutting tests
│   ├── app.jsx          # Root component
│   └── main.jsx         # Vite entry
├── public/              # Static assets
├── vite.config.js
├── vitest.config.js
└── TESTING.md           # Frontend test conventions
```

### Conventions

- **File names:** components in `PascalCase.jsx`, hooks in `useThing.js`, utilities in `kebab-case.js`.
- **Co-location:** keep a component, its tests, and its styles together. Tests sit next to the component as `Foo.test.jsx`.
- **Pages vs. sections vs. components:** a *page* is a route entry point, a *section* is a feature-scoped composition, a *component* is reusable across features.
- **State:** server state goes in TanStack Query, client state in Zustand. Don't mix them.
- **Imports:** absolute imports via the `src/` alias are preferred over deep relative paths.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).

### Contributing

Before submitting a PR:

1. Ensure `yarn install` has been run from the repo root (sets up husky)
2. Run `yarn lint` and `yarn prettier` on your changes
3. Run `yarn test:run` to ensure tests pass
4. Follow the [PR template](../.github/PULL_REQUEST_TEMPLATE.md)

See [Contributing Guide](../CONTRIBUTING.md) for more details.

### Documentation

- 📖 [Frontend Testing Guide](TESTING.md)
- 📚 [Root Testing Guide](../TESTING.md) — full pipeline, CI, coverage thresholds
- 📋 [Backend README](../futureagi/README.md)
- 📝 [Contributing Guide](../CONTRIBUTING.md)
