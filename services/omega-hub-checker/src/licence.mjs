/** Step 3, licence (SPEC §4.2 row 3): the allowlist, and a LICENSE file that matches the manifest. */
import { LICENSES } from "../vendor/omega-hub/src/index.mjs";
import { block, flag } from "./findings.mjs";

const SIGNATURES = {
  MIT: [/\bMIT License\b/i, /Permission is hereby granted, free of charge/i],
  "Apache-2.0": [/Apache License/i, /Version 2\.0/i],
  "BSD-2-Clause": [/Redistribution and use in source and binary forms/i],
  "BSD-3-Clause": [/Redistribution and use in source and binary forms/i, /Neither the name/i],
  ISC: [/Permission to use, copy, modify, and\/or distribute/i],
  "MPL-2.0": [/Mozilla Public License,? (Version|v\.?) ?2\.0/i],
  "CC0-1.0": [/CC0 1\.0 Universal|Creative Commons Legal Code/i],
  Unlicense: [/This is free and unencumbered software released into the public domain/i],
};

export function licenceFindings(manifest, entry) {
  const out = [];
  const id = manifest.license;
  if (!LICENSES.includes(id)) {
    out.push(block("licence", "R13", "omega.hub.json", `the licence ${JSON.stringify(id)} is not one of ${LICENSES.join(", ")}`, { fix: "use one of the allowed SPDX ids" }));
    return out;
  }
  for (const [rel, data] of entry) {
    if (!/^(LICEN[CS]E|COPYING)(\.[a-z]+)?$/i.test(rel)) continue;
    const text = data.toString("utf8").slice(0, 4000);
    const matches = (SIGNATURES[id] ?? []).every((pattern) => pattern.test(text));
    const two = id === "BSD-2-Clause" && /Neither the name/i.test(text);
    if (!matches || two) out.push(flag("licence", "licence-mismatch", rel, `${rel} does not read like ${id}, the manifest's licence`, { fix: "make the file and the manifest agree" }));
  }
  return out;
}
