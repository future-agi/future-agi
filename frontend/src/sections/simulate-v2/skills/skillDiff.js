/*
  A line diff for skill versions (LCS): [{ t: " " | "+" | "-", line }].
  Small files only — a SKILL.md stays under 500 lines.
*/
export const lineDiff = (a = "", b = "") => {
  const x = String(a).split("\n");
  const y = String(b).split("\n");
  const n = x.length;
  const m = y.length;
  const lcs = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i -= 1)
    for (let j = m - 1; j >= 0; j -= 1)
      lcs[i][j] =
        x.at(i) === y.at(j)
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (x.at(i) === y.at(j)) {
      out.push({ t: " ", line: x.at(i) });
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ t: "-", line: x.at(i) });
      i += 1;
    } else {
      out.push({ t: "+", line: y.at(j) });
      j += 1;
    }
  }
  while (i < n) {
    out.push({ t: "-", line: x.at(i) });
    i += 1;
  }
  while (j < m) {
    out.push({ t: "+", line: y.at(j) });
    j += 1;
  }
  return out;
};

/** Files added, removed or changed between two versions' file lists. */
export const filesDiff = (from = [], to = []) => {
  const paths = [...new Set([...from, ...to].map((f) => f.path))];
  return paths
    .map((path) => {
      const a = from.find((f) => f.path === path);
      const b = to.find((f) => f.path === path);
      if (a && b && a.text === b.text) return null;
      return {
        path,
        change: !a ? "added" : !b ? "removed" : "changed",
        lines: lineDiff(a?.text || "", b?.text || ""),
      };
    })
    .filter(Boolean);
};
