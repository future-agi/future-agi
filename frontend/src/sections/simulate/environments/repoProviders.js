// Git hosts for the Source-repository panel. Only GitHub is wired this phase;
// GitLab and Bitbucket render dimmed with a coming-soon tooltip.
export const REPO_PROVIDERS = [
  { id: "github", name: "GitHub", icon: "eva:github-fill" },
  // `logos:gitlab` renders as a tiny orange tanuki that reads as a dot at 13px;
  // the monochrome mdi mark fills the glyph box and tints like the others.
  { id: "gitlab", name: "GitLab", icon: "mdi:gitlab", comingSoon: true },
  {
    id: "bitbucket",
    name: "Bitbucket",
    icon: "logos:bitbucket",
    comingSoon: true,
  },
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
