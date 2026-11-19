import { contextProposalFromPacket } from 'src/api/skills/j1-migration-context';

// Pure reader for a local-inventory migration packet. It only reshapes the file
// into the same preview inputs a user would otherwise choose one by one.
const MAX_PACKET_BYTES = 40 * 1024 * 1024;

function bytesOf(base64) {
  const text = atob(base64);
  const bytes = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i += 1) bytes[i] = text.charCodeAt(i);
  return bytes;
}

export function packetToSources(packet) {
  if (packet?.kind !== 'futureagi-migration-packet' || packet.schema !== 1 || !Array.isArray(packet.sources) || !packet.sources.length || packet.sources.length > 50) {
    throw new Error('Choose a migration packet written by the local inventory.');
  }
  const sources = packet.sources.map((source) => {
    if (source?.kind === 'langfuse' && source.export && typeof source.export === 'object') return { exportData: source.export };
    if (source?.kind === 'folder' && Array.isArray(source.files) && source.files.length && source.source_identities && typeof source.source_identities === 'object') {
      const form = new FormData();
      for (const file of source.files) {
        if (typeof file?.path !== 'string' || typeof file.content_base64 !== 'string') throw new Error('The migration packet has an invalid file.');
        form.append('files', new Blob([bytesOf(file.content_base64)]), file.path);
      }
      if (Object.keys(source.source_identities).length) form.append('source_identities', JSON.stringify(source.source_identities));
      return { form };
    }
    throw new Error('The migration packet has an unsupported source.');
  });
  return { sources, proposal: contextProposalFromPacket(packet) };
}

export async function packetSources(file) {
  if (!file || file.size > MAX_PACKET_BYTES) throw new Error('Choose one migration packet, at most 40 MiB.');
  let packet;
  // FileReader works everywhere a user can choose a file (Blob.text may not).
  const text = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(reader.error); reader.readAsText(file); });
  try { packet = JSON.parse(text); } catch { throw new Error('Choose a migration packet written by the local inventory.'); }
  return packetToSources(packet);
}
