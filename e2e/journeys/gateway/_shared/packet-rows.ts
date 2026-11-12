// Provenance: all eleven JOURNEYS-708/packets/<journey>/<leg-or-gateway>/PACKET.json
// assertion inventories, reviewed by GATEWAY-PACKETS-SOL.json. These are row IDs,
// NOT evidence that any row ran. Keep packet-local spelling (A01 is not global).
export const PACKET_ROWS = {
  'J27-L3': ['L3-A01', 'L3-A02', 'L3-A03', 'L3-A04', 'L3-A05', 'L3-A06', 'L3-A07', 'L3-A08', 'L3-A09', 'L3-A10'],
  'J27-L4': ['L4-A00', 'L4-A01', 'L4-A02', 'L4-A03', 'L4-A04', 'L4-A05', 'L4-A06', 'L4-A07', 'L4-A08', 'L4-A09', 'L4-A10'],
  'J27-L5': ['L5-A01', 'L5-A02', 'L5-A03', 'L5-A04', 'L5-A05', 'L5-A06', 'L5-A07', 'L5-A08', 'L5-A09', 'L5-A10'],
  'J27-L6': ['A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A08', 'A09'],
  J11: [
    'W01-01', 'W01-02', 'W01-03', 'W01-04', 'W01-05', 'W01-06', 'W01-07', 'W01-08', 'W01-09', 'W01-10', 'W01-11',
    'W02-01', 'W02-02', 'W02-03', 'W02-04', 'W02-05', 'W02-06', 'W02-07', 'W02-08',
    'CLOUD-01', 'CLOUD-02', 'CLOUD-03', 'CLOUD-04',
    'COMMON-01', 'COMMON-02', 'COMMON-03', 'COMMON-04', 'COMMON-05', 'COMMON-06', 'COMMON-07', 'COMMON-08', 'COMMON-09', 'COMMON-10',
  ],
  J12: ['A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A08', 'A09', 'A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'A16', 'A17', 'A18', 'A19', 'A20', 'A21', 'A22', 'A23', 'A24', 'A25'],
  J13: [
    'W03-01', 'W03-02', 'W03-03', 'W03-04', 'W03-05', 'W03-06', 'W03-07', 'W03-08', 'W03-09', 'W03-10', 'W03-11', 'W03-12', 'W03-13', 'W03-14',
    'W04-01', 'W04-02', 'W04-03', 'W04-04', 'W04-05', 'W04-06', 'W04-07', 'W04-08',
    'COMMON-01', 'COMMON-02', 'COMMON-03', 'COMMON-04', 'COMMON-05', 'COMMON-06', 'COMMON-07', 'COMMON-08',
  ],
  J14: [
    'COMMON.1', 'W07.1', 'W07.2', 'W08.1', 'W08.2', 'W08.3', 'W09.1', 'W09.2', 'W10.1', 'W10.2',
    'W11.1', 'W11.2', 'W11.3', 'W12.1', 'W12.2', 'ATTACH.1', 'ATTACH.2', 'CC.1', 'CC.2', 'CC.3', 'CC.4', 'COMMON.2',
  ],
  J15: [
    'L1.C01', 'L1.C02', 'L1.C03', 'L1.C04', 'L1.C05', 'L1.C06', 'L1.C07', 'L1.C08', 'L1.C09', 'L1.C10', 'L1.C11', 'L1.C12',
    'W05.A01', 'W05.A02', 'W05.A03', 'W05.A04', 'W05.A05', 'W05.A06',
    'W13.A01', 'W13.A02', 'W13.A03', 'W13.A04', 'W13.A05',
    'W14.A01', 'W14.A02', 'W14.A03', 'W14.A04',
    'W15.A01', 'W15.A02', 'W15.A03', 'W15.A04',
    'W16.A01', 'W16.A02', 'W16.A03', 'W16.A04', 'W16.A05',
    'W17.A01', 'W17.A02', 'W17.A03', 'W17.A04', 'W17.A05',
    'W18.A01', 'W18.A02', 'W18.A03', 'W18.A04', 'W18.A05', 'W18.A06',
  ],
  J29: ['J29-GW-A01', 'J29-GW-A02', 'J29-GW-A03', 'J29-GW-A04', 'J29-GW-A05', 'J29-GW-A06', 'J29-GW-A07', 'J29-GW-A08', 'J29-GW-A09', 'J29-GW-A10', 'J29-GW-A11', 'J29-GW-A12'],
  J31: ['A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A08', 'A09', 'A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'A16', 'A17', 'A18', 'A19', 'A20', 'A21', 'A22', 'A23', 'A24'],
} as const;

export type PacketId = keyof typeof PACKET_ROWS;
export type AssertionRow<P extends PacketId> = (typeof PACKET_ROWS)[P][number];
export const PACKET_IDS = Object.keys(PACKET_ROWS) as PacketId[];

export function requirePacket(id: string): asserts id is PacketId {
  if (!Object.hasOwn(PACKET_ROWS, id)) throw new Error('Unknown gateway journey packet');
}

export function requireRow(packet: PacketId, row: string): void {
  requirePacket(packet);
  if (!(PACKET_ROWS[packet] as readonly string[]).includes(row)) {
    throw new Error(`Unknown assertion row for ${packet}`);
  }
}
