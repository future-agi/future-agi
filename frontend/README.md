# Frontend

The Future AGI web app — a React 18 + Vite SPA that talks to the Django backend in [`futureagi/`](../futureagi/).

> **Setup is handled at the repo root.** `docker compose up -d` from the root starts the frontend dev server (and everything else). Pre-commit hooks are installed via `yarn install` from the repo root — see the root [`README.md`](../README.md) and [`CONTRIBUTING.md`](../CONTRIBUTING.md).

## Tech Stack

- ✅ **React 18** + **Vite** (JavaScript / JSX)
- ✅ **MUI v5** + AG Grid + MUI X Data Grid for UI
- ✅ **TanStack Query** for server state, **Zustand** for client state
- ✅ **React Router v6** for routing
- ✅ **React Hook Form** + **Yup** / **Zod** for forms
- ✅ **Vitest** + **React Testing Library** for tests
- ✅ **ESLint** (Airbnb) + **Prettier** for lint/format
- ✅ **Storybook** for component development

## Project Layout

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

## Conventions

- **File names:** use `snake_case` for new files (`user_profile.jsx`, `use_auth.js`, `format_date.js`). The codebase is migrating from `camelCase` / `PascalCase` to `snake_case` — please follow `snake_case` going forward.
- **Co-location:** keep a component, its tests, and its styles together. Tests sit next to the component as `foo.test.jsx`.
- **Pages vs. sections vs. components:** a *page* is a route entry point, a *section* is a feature-scoped composition, a *component* is reusable across features.
- **State:** server state goes in TanStack Query, client state in Zustand. Don't mix them.
- **Imports:** absolute imports via the `src/` alias are preferred over deep relative paths.
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) — see [`../CONTRIBUTING.md`](../CONTRIBUTING.md).
- **Pre-commit hooks** auto-run ESLint + Prettier on staged `frontend/src/**` files. Make sure they're set up by running `yarn install` from the repo root.

## Documentation

- 📖 [Frontend Testing Guide](TESTING.md)
- 📚 [Root Testing Guide](../TESTING.md) — full pipeline, CI, coverage thresholds
- 📋 [Backend README](../futureagi/README.md)
- 📝 [Contributing Guide](../CONTRIBUTING.md)