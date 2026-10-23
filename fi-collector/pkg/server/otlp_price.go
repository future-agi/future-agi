package server

// The price of a request is the heap it makes live, in two parts with
// different lifetimes (see ingestBudget):
//
//   - handler: the decoded request (pdata), held from the work lease until
//     the handler returns;
//   - rows: the converted rows and what is built from them (curated
//     identities, stored-root and catalog sidecars), held until the drain
//     that writes them to ClickHouse ends, plus the drain's share of encoding
//     them.
//
// The request body itself is priced separately, on the wire pool, from its
// length.
//
// Every string in the body is decoded once and the rows refer to the same
// strings, so string bytes are priced once, in the rows part. Bytes values
// are copied by conversion, so they are priced in both parts.
//
// The per-element constants are heap bytes measured on the full path
// (decode, project header, auth stamp, ConvertWithIdentities, the pending
// sidecars) with pdata v1.20.0, Go 1.24, darwin/arm64 and linux/amd64, at
// element counts just past a power of two (where slices have the most spare
// capacity), then rounded up to at least 1.5 times the measurement.
// TestPriceCoversHeldHeap measures them again and fails if a request ever
// holds more than its price; TestPriceCoversRowJSON does the same for the
// JSON a drain writes. See otlp_scan.go for the counts.
const (
	priceRequest = 64 << 10 // request, response, routing and stamping state; decode stack
	priceRows    = 16 << 10 // the pending batch's share of slices and sidecars

	// handler part, per element (measured worst case in brackets)
	priceResourceSpans = 160 // [107]
	priceScopeSpans    = 192 // [123]
	priceSpan          = 320 // [235]
	priceEvent         = 128 // [84]
	priceLink          = 128 // [91]
	priceKeyValue      = 112 // [84, a string value]
	priceArrayValue    = 48  // [34]
	priceContainer     = 48  // [18]

	// rows part, per element
	priceRow         = 5376 // [4227] the row map and its four attribute maps, ids, times, sidecars
	priceRowKeyValue = 128  // [96] a map entry, its share of map growth, a boxed value
	priceRowNested   = 48   // [24] an array element converted to a Go slice entry
	priceRowResource = 1024 // [~600] a ResourceSpans' flattened resource map
	priceRowRoot     = 512  // [~300] a stored-root notification and the curated trace
)

// ingestPrice is the price of one request, in bytes.
type ingestPrice struct {
	handler int64 // released when the handler returns
	rows    int64 // released when the drain that stores the rows ends
}

func (p ingestPrice) total() int64 { return p.handler + p.rows }

// price turns a scan into the heap the request will hold.
func (s otlpShape) price() ingestPrice {
	handler := int64(priceRequest) +
		s.resourceSpans*priceResourceSpans +
		s.scopeSpans*priceScopeSpans +
		s.spans*priceSpan +
		s.events*priceEvent +
		s.links*priceLink +
		s.keyValues*priceKeyValue +
		s.arrayValues*priceArrayValue +
		s.containers*priceContainer +
		sizeClassBound(s.bytesValues, 0) // pdata's copy of bytes values
	rows := int64(priceRows) +
		s.spans*priceRow +
		s.keyValues*priceRowKeyValue +
		(s.arrayValues+s.containers)*priceRowNested +
		s.resourceSpansWithSpans*priceRowResource +
		s.rootSpans*priceRowRoot +
		// Strings are shared by pdata and the rows, and outlive pdata.
		sizeClassBound(s.stringBytes, s.strings) +
		sizeClassBound(s.bytesValues, 0) + // the rows' copy of bytes values
		s.hotCopyBytes +
		// Encoding at the drain: chwriter encodes one row at a time into its
		// chunk buffer (the flusher's fixed share, see ingestLimits), so a
		// request adds its largest row twice (the encoder's own buffer, and
		// the chunk buffer growing past its size). The curated rows of a
		// drain are encoded into one buffer, which can double as it grows.
		2*s.maxRowJSON +
		2*s.curatedJSONBytes
	return ingestPrice{handler: handler, rows: rows}
}

// sizeClassBound bounds the heap that n bytes in count allocations take: Go
// rounds an allocation up to its size class (at most a quarter more, plus 16
// bytes, at any length; large ones to whole pages).
func sizeClassBound(n, count int64) int64 { return n + n/4 + count*16 }
