// Git hosts for the Source-repository panel. Only GitHub is wired in this
// release; GitLab and Bitbucket are shown disabled with the shared line.
export const REPO_PROVIDERS = [
  { id: "github", name: "GitHub", icon: "simple-icons:github" },
  // `logos:gitlab` renders as a tiny orange tanuki that reads as a dot at 13px;
  // the monochrome mdi mark fills the glyph box and tints like the others.
  {
    id: "gitlab",
    name: "GitLab",
    icon: "simple-icons:gitlab",
    comingSoon: true,
  },
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
// Where the deployment refuses private GitHub sources (the harness health
// endpoint reports private_github_available: false), Private is not offered.
// The same words as the backend's PRIVATE_GITHUB_UNAVAILABLE_MESSAGE.
export const PRIVATE_GITHUB_UNAVAILABLE_MESSAGE =
  "Private GitHub repositories are not available on Future AGI cloud. Use a public repository or upload the code.";
