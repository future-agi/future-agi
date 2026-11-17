package contentbounds

import (
	"encoding/json"
	"testing"
)

func accumulator(t *testing.T, l Limits) *Accumulator {
	t.Helper()
	a, err := NewAccumulator(l)
	if err != nil {
		t.Fatal("valid accumulator refused")
	}
	return a
}

func TestAccumulatorItemAndTotalByteBoundariesAreAtomic(t *testing.T) {
	l := designLimits()
	l.MaxItemBytes = 3
	l.MaxTotalBytes = 6
	a := accumulator(t, l)
	item := scanText(t, `"x"`, designLimits())
	if a.Add(item) != nil || a.Add(item) != nil {
		t.Fatal("exact item and aggregate limits refused")
	}
	before := a.Counts()
	requireCode(t, a.Add(item), CodeTotalBytes)
	if a.Counts() != before {
		t.Fatal("byte refusal mutated accumulator")
	}
	a = accumulator(t, l)
	before = a.Counts()
	requireCode(t, a.Add(scanText(t, `"xx"`, designLimits())), CodeItemBytes)
	if a.Counts() != before {
		t.Fatal("oversized item mutated accumulator")
	}
}

func TestAccumulatorItemAndNodeCounts(t *testing.T) {
	l := designLimits()
	l.MaxItems = 2
	a := accumulator(t, l)
	item := scanText(t, `0`, l)
	if a.Add(item) != nil || a.Add(item) != nil {
		t.Fatal("item count boundary refused")
	}
	before := a.Counts()
	requireCode(t, a.Add(item), CodeItems)
	if a.Counts() != before {
		t.Fatal("item-count refusal mutated accumulator")
	}
	l = designLimits()
	l.MaxNodes = 2
	a = accumulator(t, l)
	if a.Add(item) != nil || a.Add(item) != nil {
		t.Fatal("node count boundary refused")
	}
	before = a.Counts()
	requireCode(t, a.Add(item), CodeNodes)
	if a.Counts() != before {
		t.Fatal("node refusal mutated accumulator")
	}
}

func TestAccumulatorRejectsUnvalidatedAndRechecksReceipt(t *testing.T) {
	var zero Accumulator
	requireCode(t, zero.Add(Summary{}), CodeInvalidAccumulator)
	requireCode(t, zero.ObserveCursor("x"), CodeInvalidAccumulator)
	var absent *Accumulator
	requireCode(t, absent.Add(Summary{}), CodeInvalidAccumulator)
	a := accumulator(t, designLimits())
	for _, invalid := range []Summary{{}, {counts: Counts{Bytes: 1, Nodes: 1}}} {
		requireCode(t, a.Add(invalid), CodeInvalidSummary)
	}
	if a.Counts() != (Totals{}) {
		t.Fatal("invalid receipts mutated accumulator")
	}
	cases := []struct {
		limits Limits
		input  string
		code   Code
	}{
		{Limits{1, 100, 100, 100, 10, 100}, `[]`, CodeReadBytes},
		{Limits{100, 100, 100, 100, 1, 100}, `[[]]`, CodeDepth},
		{Limits{100, 100, 100, 1, 10, 100}, `[0,1]`, CodeItems},
		{Limits{100, 100, 100, 100, 10, 1}, `[0]`, CodeNodes},
	}
	for _, tc := range cases {
		a := accumulator(t, tc.limits)
		requireCode(t, a.Add(scanText(t, tc.input, designLimits())), tc.code)
		if a.Counts() != (Totals{}) {
			t.Fatal("receipt recheck refusal mutated accumulator")
		}
	}
}

func TestSummaryCannotBeForgedThroughExportedObservations(t *testing.T) {
	original := scanText(t, `0`, designLimits())
	wire, err := json.Marshal(original.Counts())
	if err != nil {
		t.Fatal("count fixture encoding failed")
	}
	var forged Summary
	if json.Unmarshal(wire, &forged) != nil {
		t.Fatal("count fixture decoding failed")
	}
	a := accumulator(t, designLimits())
	requireCode(t, a.Add(forged), CodeInvalidSummary)
	if a.Add(original) != nil {
		t.Fatal("original validated receipt was lost")
	}
}

func TestAccumulatorTreatsEachValidatedRootAsOneItem(t *testing.T) {
	a := accumulator(t, designLimits())
	s := scanText(t, `[0,1]`, designLimits())
	if a.Add(s) != nil {
		t.Fatal("validated root refused")
	}
	if a.Counts().Items != 1 || a.Counts().Nodes != 3 || a.Counts().Depth != 1 {
		t.Fatal("accumulator implicitly flattened a root array")
	}
}

func TestCursorCyclesAndBudgetsAreAtomic(t *testing.T) {
	l := designLimits()
	l.MaxItemBytes = 2
	l.MaxTotalBytes = 4
	l.MaxItems = 2
	a := accumulator(t, l)
	if a.ObserveCursor("aa") != nil || a.ObserveCursor("bb") != nil {
		t.Fatal("cursor boundary refused")
	}
	before := a.Counts()
	requireCode(t, a.ObserveCursor("aa"), CodeCursorCycle)
	requireCode(t, a.ObserveCursor("c"), CodeCursorCount)
	requireCode(t, a.ObserveCursor("ccc"), CodeCursorBytes)
	requireCode(t, a.ObserveCursor(""), CodeInvalidCursor)
	if a.Counts() != before {
		t.Fatal("cursor refusal mutated accumulator")
	}
	l.MaxItems = 3
	a = accumulator(t, l)
	if a.ObserveCursor("aa") != nil || a.ObserveCursor("bb") != nil {
		t.Fatal("valid cursor refused")
	}
	before = a.Counts()
	requireCode(t, a.ObserveCursor("c"), CodeCursorBytes)
	if a.Counts() != before {
		t.Fatal("cursor byte refusal mutated accumulator")
	}
	// A separate accumulation has an independent cursor history.
	if accumulator(t, l).ObserveCursor("aa") != nil {
		t.Fatal("cursor state leaked between accumulations")
	}
}

func TestAccumulatorSubtractionGuardsIntegerOverflow(t *testing.T) {
	max := int64(^uint(0)>>1) - 1
	l := Limits{max, max, max, max, max, max}
	s := scanText(t, `10`, l)
	a := accumulator(t, l)
	// Private state injection exercises arithmetic near its representable limit
	// without allocating huge payloads; public summaries remain unforgeable.
	a.totals.Bytes = max - 1
	before := a.Counts()
	requireCode(t, a.Add(s), CodeTotalBytes)
	if a.Counts() != before {
		t.Fatal("overflow mutated byte state")
	}
	a = accumulator(t, l)
	a.totals.Nodes = max
	requireCode(t, a.Add(s), CodeNodes)
	a = accumulator(t, l)
	a.totals.Items = max
	requireCode(t, a.Add(s), CodeItems)
	a = accumulator(t, l)
	a.totals.CursorBytes = max - 1
	requireCode(t, a.ObserveCursor("xx"), CodeCursorBytes)
	a = accumulator(t, l)
	a.totals.Cursors = max
	requireCode(t, a.ObserveCursor("x"), CodeCursorCount)
}
