export function crc32(buf: Buffer): number;
export function readZip(buf: Buffer): { name: string; data: Buffer }[];
export function writeZip(entries: { name: string; data: Buffer }[]): Buffer;
