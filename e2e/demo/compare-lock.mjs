// Compare the installed skill lock with the support-agent production context lock (refund-policy entry).
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
const [statePath, proj] = process.argv.slice(2);
const st = JSON.parse(readFileSync(statePath, 'utf8')); const a = st.author;
const walk = (d) => readdirSync(d).flatMap((n) => statSync(join(d, n)).isDirectory() ? walk(join(d, n)) : [join(d, n)]);
const lockFiles = walk(proj).filter((f) => /lock/i.test(f));
const locks = lockFiles.map((f) => ({ file: f.replace(proj + '/', ''), text: readFileSync(f, 'utf8') }));
const h = { Authorization: `Bearer ${a.tokens.access}`, 'X-Organization-Id': a.organizationId, 'X-Workspace-Id': a.workspaceId };
const detail = await (await fetch(`http://localhost:8100/model-hub/skills/contexts/${st.support.id}/`, { headers: h })).json();
const prod = detail.result.context.labels.find((l) => l.name === 'production');
const insp = await (await fetch(`http://localhost:8100/model-hub/skills/contexts/${st.support.id}/versions/${prod.version}/inspection/`, { headers: h })).json();
const link = insp.result.descriptor.links.find((l) => l.skill_id === st.skills['refund-policy'].id);
const installedText = locks.map((l) => l.text).join('\n');
console.log(JSON.stringify({ contextProductionVersion: prod.version, contextLock: { version: link.version, digest: link.digest },
  lockFiles: locks.map((l) => l.file), installedLockContainsDigest: installedText.includes(link.digest),
  installedLock: locks.map((l) => l.text.slice(0, 600)) }, null, 2));
