package contentbounds

import (
	"strings"
	"testing"
)

func scanText(t *testing.T, input string, l Limits) Summary {
	t.Helper()
	s, err := Scan(strings.NewReader(input), l)
	if err != nil {
		t.Fatal("valid bounded fixture refused")
	}
	return s
}

func TestScanCountingContract(t *testing.T) {
	cases := []struct {
		input               string
		kind                Kind
		nodes, depth, items int64
	}{
		{"null", KindNull, 1, 0, 0},
		{" true\r\n", KindBoolean, 1, 0, 0},
		{"false", KindBoolean, 1, 0, 0},
		{"-0.25e+2", KindNumber, 1, 0, 0},
		{`"\uD83D\uDE00"`, KindString, 1, 0, 0},
		{`{}`, KindObject, 1, 1, 0},
		{`[]`, KindArray, 1, 1, 0},
		{`{"a":0}`, KindObject, 2, 1, 0},
		{`[1,[],{"x":[true]}]`, KindArray, 6, 3, 3},
		{`{"a":[1,2,3]}`, KindObject, 5, 2, 0},
		{`[{"a":0},{"a":1}]`, KindArray, 5, 2, 2},
		{`{"a":0,"A":1}`, KindObject, 3, 1, 0},
		{`{"é":0,"e\u0301":1}`, KindObject, 3, 1, 0},
		{`"\"\\\/\b\f\n\r\t"`, KindString, 1, 0, 0},
		{`"�"`, KindString, 1, 0, 0},
	}
	for _, tc := range cases {
		s := scanText(t, tc.input, designLimits())
		c := s.Counts()
		if c.Bytes != int64(len(tc.input)) || c.Nodes != tc.nodes || c.Depth != tc.depth || c.RootArrayItems != tc.items || s.Kind() != tc.kind {
			t.Fatal("incorrect count or root kind")
		}
		c.Bytes = 0
		if s.Counts().Bytes == 0 {
			t.Fatal("count snapshot mutated the validation receipt")
		}
	}
}

func TestScanByteBoundaryIncludesWhitespace(t *testing.T) {
	for _, input := range []string{`0`, `"abc"`, " [] \r\n", `{"a":"é"}`} {
		l := designLimits()
		l.MaxReadBytes = int64(len(input))
		scanText(t, input, l)
		_, err := Scan(strings.NewReader(input+" "), l)
		requireCode(t, err, CodeReadBytes)
	}
}

func TestScanStructuralBoundaries(t *testing.T) {
	l := designLimits()
	l.MaxDepth = 1
	scanText(t, `{"a":0}`, l)
	_, err := Scan(strings.NewReader(`{"a":[]}`), l)
	requireCode(t, err, CodeDepth)
	l.MaxDepth = 2
	scanText(t, `[[0],{}]`, l)
	_, err = Scan(strings.NewReader(`[[[]]]`), l)
	requireCode(t, err, CodeDepth)
	l = designLimits()
	l.MaxNodes = 3
	scanText(t, `{"a":0,"b":1}`, l)
	_, err = Scan(strings.NewReader(`{"a":0,"b":1,"c":2}`), l)
	requireCode(t, err, CodeNodes)
	l.MaxNodes = 1
	scanText(t, `[]`, l)
	scanText(t, `"x"`, l)
	_, err = Scan(strings.NewReader(`[0]`), l)
	requireCode(t, err, CodeNodes)
	l = designLimits()
	l.MaxItems = 2
	scanText(t, `[[],[1,2,3,4]]`, l)
	scanText(t, `{"x":[1,2,3,4]}`, l)
	_, err = Scan(strings.NewReader(`[0,1,2]`), l)
	requireCode(t, err, CodeItems)
}

func TestScanMalformedAndTruncated(t *testing.T) {
	cases := []string{
		"", " ", `+1`, `01`, `-01`, `1.`, `1e`, `1e+`, `.1`, `--1`,
		`NaN`, `Infinity`, `undefined`, `True`, `tru`, `nullx`, `true false`,
		`[`, `{`, `}`, `]`, `[1,]`, `[,1]`, `[1 2]`, `{"a"}`, `{"a":}`,
		`{a:0}`, `{"a":0,}`, `{"a":0 "b":1}`, `{"a"::0}`, `[]0`, `{}[]`,
		`"`, `"abc`, `"\`, `"\x00"`, `"\u123"`, `"\uZZZZ"`,
		`"\uD800"`, `"\uDC00"`, `"\uD800\u0041"`, `"\uD800x"`,
		`"\uD800\uD800"`, `"\uDC00\uD800"`, "\ufeff0", "0\u00a0",
		"\"\n\"", "\"\x00\"", "\"\x80\"", "\"\xc0\xaf\"",
		"\"\xed\xa0\x80\"", "\"\xf4\x90\x80\x80\"", "\"\xe2\x82\"",
		`/*x*/0`, `0//x`, "0\v", `{"a":0}:0`, `{"a":0,[]:1}`,
	}
	for _, input := range cases {
		s, err := Scan(strings.NewReader(input), designLimits())
		requireCode(t, err, CodeJSON)
		if s != (Summary{}) {
			t.Fatal("refusal returned a usable summary")
		}
	}
}

func TestScanSemanticDuplicateKeys(t *testing.T) {
	cases := []string{
		`{"a":0,"a":1}`, `{"a":0,"\u0061":1}`,
		`{"/":0,"\/":1}`, `{"é":0,"\u00e9":1}`,
		`{"𝄞":0,"\uD834\uDD1E":1}`, `{"\n":0,"\u000a":1}`,
		`{"\\":0,"\u005c":1}`, `{"":0,"":1}`,
		`{"\u0000":0,"\u0000":1}`, `{"x":{"a":0,"\u0061":1}}`,
	}
	for _, input := range cases {
		_, err := Scan(strings.NewReader(input), designLimits())
		requireCode(t, err, CodeDuplicateKey)
	}
}

func TestScanEveryTruncatedPrefixOfComposite(t *testing.T) {
	input := `{"a":["\uD834\uDD1E",true,false,null,-1.2e+3,{"é":0}]}`
	for n := 0; n < len(input); n++ {
		_, err := Scan(strings.NewReader(input[:n]), designLimits())
		requireCode(t, err, CodeJSON)
	}
	scanText(t, input, designLimits())
}

func TestScanArrayDoesNotSelectProtocolEnvelopes(t *testing.T) {
	l := designLimits()
	l.MaxItems = 1
	s := scanText(t, `{"result":[0,1,2],"other":[3,4]}`, l)
	if s.Counts().RootArrayItems != 0 {
		t.Fatal("scanner inferred a protocol envelope")
	}
	// A generic root read is not a list item. Add separately enforces item bytes.
	l.MaxItemBytes = 1
	scanText(t, `"bounded read, larger than a list item"`, l)
}
