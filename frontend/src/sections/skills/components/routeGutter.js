// The one wrapper every Skills route root uses (UX iteration 3, SH5), so the
// first element of each area sits at the same height under the Skills
// navigation and the page doesn't jump when you switch tabs. Measured on the
// library: its title starts 24px below the navigation from md up (y=68 at
// 1440) and 16px below it on phones (y=116 at 390).
export const skillsRouteSx = { px: { xs: 2, md: 3 }, pt: { xs: 2, md: 3 }, pb: 2, minWidth: 0 };
