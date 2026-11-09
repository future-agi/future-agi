// Git hosts for the Source-repository panel. Only GitHub is wired this phase;
// Unimplemented providers are not offered.
export const REPO_PROVIDERS = [
  { id: "github", name: "GitHub", icon: "eva:github-fill" },
];

export const DEFAULT_REPO_PROVIDER = "github";
export const DEFAULT_BRANCH = "main";

// Repository visibility. Private repos need a GitHub App installation to grant
// the runner read access; public repos need nothing extra.
export const REPO_VISIBILITY = { PUBLIC: "public", PRIVATE: "private" };
export const DEFAULT_REPO_VISIBILITY = "public";
export const REPO_VISIBILITY_LABEL = {
  [REPO_VISIBILITY.PUBLIC]: "Public",
  [REPO_VISIBILITY.PRIVATE]: "Private",
};
export const INSTALLATION_ID_LABEL = "GitHub App installation ID";
export const INSTALLATION_ID_HELPER =
  "The installation that grants the runner access to this repository.";
export const INSTALLATION_ID_PLACEHOLDER = "12345678";
