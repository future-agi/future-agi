# Branch Naming Convention

## 🎯 Overview

This document defines the branch naming conventions for this project to ensure consistency, clarity, and automated workflow compatibility.

## 📋 Branch Naming Rules

### Format
```
<type>/<ticket-id>-<short-description>
```

### Components

#### 1. **Type** (Required)

Matches the Conventional Commits prefixes used in `CONTRIBUTING.md`.

| Type | Description | Example |
|------|-------------|---------|
| `feat` | New features or enhancements | `feat/AUTH-123-user-login` |
| `fix` | Bug fixes | `fix/BUG-456-login-error` |
| `chore` | Maintenance tasks, build changes | `chore/BUILD-404-upgrade-deps` |
| `docs` | Documentation updates | `docs/DOC-202-update-readme` |
| `refactor` | Code refactoring without behavior changes | `refactor/TECH-101-cleanup-utils` |
| `test` | Adding or updating tests | `test/TEST-303-add-unit-tests` |
| `perf` | Performance improvements | `perf/PERF-500-optimize-query` |

#### 2. **Ticket ID** (Optional but Recommended)
- Use your project management system ticket ID
- Examples: `JIRA-123`, `GH-456`, `TICKET-789`
- If no ticket exists, use descriptive identifier

#### 3. **Short Description** (Required)
- Use kebab-case (lowercase with hyphens)
- Keep it concise but descriptive
- Maximum 50 characters recommended

## ✅ Valid Examples

```bash
# Features
feat/AUTH-123-user-authentication
feat/PAY-456-stripe-integration
feat/search-functionality

# Bug fixes
fix/LOGIN-789-password-reset
fix/UI-101-button-alignment
fix/memory-leak

# Other types
refactor/TECH-222-restructure-components
docs/update-testing-guide
test/add-integration-tests
chore/UPDATE-333-upgrade-react
perf/PERF-500-list-view-pagination
```

## ❌ Invalid Examples

```bash
# Bad: No type
user-login-feature

# Bad: Disallowed type (use `feat` / `fix`, not the long forms)
feature/user-login
bugfix/button-alignment
hotfix/security-patch
release/v1.2.3

# Bad: Spaces
feat/user login page

# Bad: CamelCase
feat/userLoginPage

# Bad: Too vague
fix/fix

# Bad: Special characters
feat/user@login!

# Bad: Too long
feat/TICKET-123-implement-comprehensive-user-authentication-system-with-oauth-and-2fa
```

## 🚀 Branch Lifecycle

### Main Branch
- `main` — production-ready code; PRs target this directly (see `CONTRIBUTING.md`).

### Workflow
1. Branch from `main` using a valid `type/short-description` name.
2. Make focused changes — keep the diff small.
3. Open a PR against `main`.
4. After review and merge, delete the branch.

## 🛠️ Enforcement

### Local Validation
- Pre-push Git hook validates branch names
- Prevents pushing branches with invalid names

### CI/CD Integration
- GitHub Actions validate branch names
- Automated checks on all pushes

### GitHub Settings
- Branch protection rules enforce naming
- Required status checks for valid names

## 📝 Configuration

### IDE Integration
Configure your IDE to suggest branch names:

**VS Code Settings:**
```json
{
  "git.branchPrefix": "feat/",
  "git.branchSuggestions": [
    "feat/",
    "fix/",
    "chore/",
    "docs/",
    "refactor/",
    "test/",
    "perf/"
  ]
}
```

### Branch Creation Helper Script
Use the provided script for easy branch creation:
```bash
# Create a feat branch with ticket ID
./scripts/create-branch.sh feat user-authentication AUTH-123

# Create a fix branch with ticket ID
./scripts/create-branch.sh fix login-error BUG-456

# Create a docs branch without ticket ID
./scripts/create-branch.sh docs update-readme
```

The script will:
- ✅ Validate the branch type and description
- ✅ Check if the branch already exists
- ✅ Create and checkout the new branch
- ✅ Provide next steps guidance

### Git Aliases
Add to your `.gitconfig`:
```bash
[alias]
  new-feat = "!f() { git checkout -b feat/$1; }; f"
  new-fix = "!f() { git checkout -b fix/$1; }; f"
  new-chore = "!f() { git checkout -b chore/$1; }; f"
```

Usage:
```bash
git new-feat TICKET-123-user-login
git new-fix BUG-456-button-alignment
git new-chore UPDATE-333-upgrade-react
```

## 🎯 Best Practices

### DO:
- ✅ Use descriptive names that explain the purpose
- ✅ Include ticket/issue numbers when available
- ✅ Keep descriptions concise but clear
- ✅ Use consistent formatting across the team
- ✅ Delete branches after merging

### DON'T:
- ❌ Use personal identifiers in branch names
- ❌ Create branches with generic names like "fix" or "update"
- ❌ Use special characters or spaces
- ❌ Create long-lived feature branches
- ❌ Push directly to main branches

## 🔧 Troubleshooting

### Branch Name Rejected?
```bash
# Check current branch name
git branch --show-current

# Rename current branch
git branch -m new-valid-name

# Delete remote branch and push with new name
git push origin --delete old-branch-name
git push origin -u new-valid-name
```

### Common Issues
1. **Spaces in names** → Use hyphens instead
2. **Missing type prefix** → Add appropriate type/
3. **Too long** → Shorten description
4. **Special characters** → Use only letters, numbers, hyphens

## 📊 Validation Rules

The following regex pattern validates branch names:
```regex
^(feat|fix|chore|docs|refactor|test|perf)\/[a-zA-Z0-9]+([a-zA-Z0-9\-]*[a-zA-Z0-9])?$
```

### Pattern Breakdown:
- `^` - Start of string
- `(feat|fix|chore|docs|refactor|test|perf)` - Valid types (Conventional Commits)
- `\/` - Forward slash separator
- `[a-zA-Z0-9]+` - At least one alphanumeric character
- `([a-zA-Z0-9\-]*[a-zA-Z0-9])?` - Optional additional chars with hyphens
- `$` - End of string

## 🎉 Benefits

- **Consistency**: Uniform naming across the team
- **Automation**: CI/CD workflows trigger correctly
- **Organization**: Easy to understand branch purpose
- **Integration**: Works with project management tools
- **Cleanup**: Easier to identify and remove old branches

---

## Quick Reference Card

```
Types: feat, fix, chore, docs, refactor, test, perf
Format: type/TICKET-123-short-description (ticket optional)
Examples:
  ✅ feat/AUTH-123-user-login
  ✅ fix/UI-456-button-alignment
  ✅ docs/update-readme
  ❌ feature/user-login        (use `feat`)
  ❌ bugfix/button-fix         (use `fix`)
  ❌ feat/user login           (no spaces)
  ❌ myFeatureBranch           (no type prefix)
```

For questions or suggestions, please create an issue or reach out to the development team. 