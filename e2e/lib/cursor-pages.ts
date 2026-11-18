// Test-side oracle for bounded list chunks (list_cursor.bounded_chunk_complete).
// Deliberately independent of the product paginator: complete chunks need not
// fill a visible page, and an advancing empty checkpoint is not end-of-data.
export interface ListChunk<Row> {
  table: Row[];
  metadata: {
    total_rows: number;
    total_rows_is_lower_bound?: boolean;
    has_more?: boolean;
    next_cursor?: string | null;
    query_complete?: boolean;
    query_status?: string;
    query_error_code?: string | null;
  };
}

export class CursorPageAccumulator<Row> {
  private readonly identify: (row: Row) => string;
  private readonly pageSize: number;
  private readonly maxHops: number;
  private readonly allowNumbered: boolean;
  private readonly seenIds = new Set<string>();
  private readonly seenCursors = new Set<string>();
  private readonly rows: Row[] = [];
  private readonly overflow: Row[] = [];
  private hops = 0;
  private mode: 'cursor' | 'numbered' | undefined;
  private last: ListChunk<Row>['metadata'] | undefined;
  private ended = false;

  constructor(pageSize: number, identify: (row: Row) => string, options: { maxHops?: number; allowNumbered?: boolean } = {}) {
    this.pageSize = pageSize;
    this.identify = identify;
    this.maxHops = options.maxHops ?? 8;
    this.allowNumbered = options.allowNumbered ?? false;
    if (!Number.isInteger(pageSize) || pageSize < 1 || !Number.isInteger(this.maxHops) || this.maxHops < 1)
      throw new Error('Positive integer page size and hop limit required');
  }

  get terminal() { return this.ended; }
  get pageReady() { return this.overflow.length >= this.pageSize || this.ended; }
  get bufferedCount() { return this.overflow.length; }
  get allRows() { return this.rows.slice(); }
  get metadata() { return this.last; }

  // Cursor hops omit the numbered offset, as on the real wire. Usage sorting
  // explicitly opts into the legacy numbered contract (no has_more field).
  nextQuery(root: Record<string, unknown>): Record<string, unknown> {
    if (this.ended) throw new Error('Cannot load after a terminal response');
    if (this.hops >= this.maxHops)
      throw new Error(`List did not reach a terminal response within ${this.maxHops} transport hops`);
    if (this.hops === 0) return { ...root };
    if (this.mode === 'numbered') return { ...root, page_number: this.hops };
    const { page_number: _page, ...query } = root;
    return { ...query, cursor: this.last!.next_cursor };
  }

  append(chunk: ListChunk<Row>): void {
    // Enforce the bound even when a caller does not use nextQuery.
    this.nextQuery({});
    const { table, metadata } = chunk;
    const mode = typeof metadata.has_more === 'boolean' ? 'cursor' : 'numbered';
    if (mode === 'numbered' && (!this.allowNumbered || metadata.has_more !== undefined))
      throw new Error('Expected an explicit has_more cursor contract');
    if (this.mode && this.mode !== mode) throw new Error('List changed pagination contract mid-walk');
    if (!Number.isInteger(metadata.total_rows) || metadata.total_rows < 0)
      throw new Error('List total must be a nonnegative integer');
    // Chunks may be short, never oversized: the server slices at most
    // page_size rows per response, so a larger chunk means page_size was ignored.
    if (table.length > this.pageSize)
      throw new Error(`Chunk of ${table.length} rows exceeds the requested page_size ${this.pageSize}`);
    const incoming = table.map(this.identify);
    const unique = new Set(incoming);
    if (incoming.some(id => !id || this.seenIds.has(id)) || unique.size !== incoming.length)
      throw new Error('Duplicate or missing row ID in list walk');
    const count = this.rows.length + table.length;
    let terminal: boolean;
    if (mode === 'cursor') {
      terminal = metadata.has_more === false;
      if (!terminal) {
        const cursor = metadata.next_cursor;
        if (typeof cursor !== 'string' || cursor.length === 0)
          throw new Error('Nonterminal chunk requires a continuation cursor');
        if (this.seenCursors.has(cursor)) throw new Error('Stalled or cycling continuation cursor');
        if (metadata.total_rows_is_lower_bound !== true)
          throw new Error('Nonterminal cursor total must be a lower bound');
        // cursor_page_metadata: the rows seen so far, plus one only when an
        // unseen match beyond this chunk is already proven.
        if (metadata.total_rows !== count && metadata.total_rows !== count + 1)
          throw new Error(`Nonterminal lower bound ${metadata.total_rows} must be ${count} seen rows or ${count + 1}`);
        this.seenCursors.add(cursor);
      } else if (metadata.next_cursor != null) {
        throw new Error('Terminal response must not carry a continuation cursor');
      }
    } else {
      if (metadata.next_cursor != null || metadata.total_rows_is_lower_bound === true)
        throw new Error('Numbered fallback requires an exact total without a cursor');
      if (this.last && metadata.total_rows !== this.last.total_rows)
        throw new Error('Numbered total changed mid-walk');
      if (count > metadata.total_rows || (count < metadata.total_rows && table.length !== this.pageSize))
        throw new Error('Numbered page does not advance toward its exact total');
      terminal = count === metadata.total_rows;
    }
    if (terminal && (count !== metadata.total_rows || (mode === 'cursor' && metadata.total_rows_is_lower_bound !== false)))
      throw new Error('Terminal response must report the exact accumulated total');
    incoming.forEach(id => this.seenIds.add(id));
    this.rows.push(...table);
    this.overflow.push(...table);
    this.last = metadata;
    this.mode = mode;
    this.ended = terminal;
    this.hops += 1;
  }

  takePage(): Row[] {
    if (!this.pageReady) throw new Error('Logical page is not filled and no terminal response was observed');
    return this.overflow.splice(0, this.pageSize);
  }

  assertTerminal(): void {
    if (!this.ended) throw new Error('List walk stopped without a terminal response');
  }
}
