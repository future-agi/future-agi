package canonjson

import (
	"math"
	"reflect"
	"strings"
	"testing"
)

// RFC 8785 Appendix B: IEEE 754 bit patterns and their canonical text.
func TestNumberTextRFC8785(t *testing.T) {
	for bits, want := range map[uint64]string{
		0x0000000000000000: "0",
		0x8000000000000000: "0",
		0x0000000000000001: "5e-324",
		0x8000000000000001: "-5e-324",
		0x7fefffffffffffff: "1.7976931348623157e+308",
		0xffefffffffffffff: "-1.7976931348623157e+308",
		0x4340000000000000: "9007199254740992",
		0xc340000000000000: "-9007199254740992",
		0x4430000000000000: "295147905179352830000",
		0x44b52d02c7e14af5: "9.999999999999997e+22",
		0x44b52d02c7e14af6: "1e+23",
		0x44b52d02c7e14af7: "1.0000000000000001e+23",
		0x444b1ae4d6e2ef4e: "999999999999999700000",
		0x444b1ae4d6e2ef4f: "999999999999999900000",
		0x444b1ae4d6e2ef50: "1e+21",
		0x3eb0c6f7a0b5ed8c: "9.999999999999997e-7",
		0x3eb0c6f7a0b5ed8d: "0.000001",
		0x41b3de4355555553: "333333333.3333332",
		0x41b3de4355555554: "333333333.33333325",
		0x41b3de4355555555: "333333333.3333333",
		0x41b3de4355555556: "333333333.3333334",
		0x41b3de4355555557: "333333333.33333343",
		0xbecbf647612f3696: "-0.0000033333333333333333",
		0x43143ff3c1cb0959: "1424953923781206.2",
	} {
		got, err := NumberText(math.Float64frombits(bits))
		if err != nil || got != want {
			t.Errorf("%#x: got %q, %v; want %q", bits, got, err, want)
		}
	}
	for _, bad := range []any{math.NaN(), math.Inf(1), int64(1<<53 + 1)} {
		if _, err := NumberText(bad); err == nil {
			t.Errorf("%v: want an error", bad)
		}
	}
}

func TestCanonicalSortsByUTF16AndEscapesMinimally(t *testing.T) {
	v := Object{
		{"\ufb33", int64(3)}, {"\U0001F600", int64(2)}, {"\u20ac", int64(1)},
		{"b", []any{true, nil, "é\u0001\n\"\\\u2028"}}, {"a", Object{{"y", 1.5}, {"x", 1e21}}},
	}
	got, err := Canonical(v)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"a":{"x":1e+21,"y":1.5},"b":[true,null,"é\u0001\n\"\\` + "\u2028" + `"],"€":1,"😀":2,"דּ":3}`
	if string(got) != want {
		t.Errorf("got  %s\nwant %s", got, want)
	}
}

// The Prettier 3.3.0 probe outputs (lanes/CN6/scratch/prettier, run with the
// repository's frontend/node_modules prettier): Pretty reproduces each one
// from its input and from itself.
func TestPrettyMatchesPrettierProbes(t *testing.T) {
	for name, c := range map[string]struct{ in, out string }{
		"objects, arrays, widths": {
			in: `{"a":[{"x":1}],"b":[["p","q"],["r"]],"c":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],` +
				`"d":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],"e":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],` +
				`"f":[{},[]],"g":[1,2],"h":"a very long string value that goes well beyond the eighty column print width of prettier for sure","i":[["p"],["q"]],"j":{}}`,
			out: `{
  "a": [
    {
      "x": 1
    }
  ],
  "b": [["p", "q"], ["r"]],
  "c": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ],
  "d": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ],
  "e": [
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ],
  "f": [{}, []],
  "g": [1, 2],
  "h": "a very long string value that goes well beyond the eighty column print width of prettier for sure",
  "i": [["p"], ["q"]],
  "j": {}
}
`,
		},
		"the 80-column edge, with and without a comma": {
			in: `{"k80c":["aaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],"k81c":["aaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],` +
				`"k79c":["aaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],"z80":["aaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]}`,
			out: `{
  "k80c": ["aaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
  "k81c": [
    "aaaaaaaaaa",
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  ],
  "k79c": ["aaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
  "z80": ["aaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
}
`,
		},
		"numbers, literals, arrays of arrays": {
			in: `{"c":[1.5,-2,1e+21,0.000001,1e-7],"d":[true,false,null],"e":[[1,2],[3,4]],"f":[[],[]],"g":[["a"],{}],"s":"\u00e9\u007f\b\f\u001f"}`,
			out: `{
  "c": [1.5, -2, 1e21, 0.000001, 1e-7],
  "d": [true, false, null],
  "e": [
    [1, 2],
    [3, 4]
  ],
  "f": [[], []],
  "g": [["a"], {}],
  "s": "\u00e9\u007f\b\f\u001f"
}
`,
		},
	} {
		for _, src := range []string{c.in, c.out} {
			v, err := Parse([]byte(src))
			if err != nil {
				t.Fatalf("%s: %v", name, err)
			}
			got, err := Pretty(v)
			if err != nil {
				t.Fatalf("%s: %v", name, err)
			}
			if string(got) != c.out {
				t.Errorf("%s:\ngot\n%s\nwant\n%s", name, got, c.out)
			}
		}
	}
}

func TestPrettyFillsNumbers(t *testing.T) {
	nums := make([]any, 30)
	for i := range nums {
		nums[i] = int64(1000 + i)
	}
	got, err := Pretty(Object{{"n", nums}})
	if err != nil {
		t.Fatal(err)
	}
	// Prettier 3.3.0's output for {"n": [1000, ..., 1029]}.
	want := `{
  "n": [
    1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 1011,
    1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019, 1020, 1021, 1022, 1023,
    1024, 1025, 1026, 1027, 1028, 1029
  ]
}
`
	if string(got) != want {
		t.Errorf("got\n%s\nwant\n%s", got, want)
	}
}

func TestParseIsStrictAndKeepsOrder(t *testing.T) {
	v, err := Parse([]byte(`{"z":1,"a":[2.5,9223372036854775808],"m":null}`))
	if err != nil {
		t.Fatal(err)
	}
	want := Object{{"z", int64(1)}, {"a", []any{2.5, 9223372036854775808.0}}, {"m", nil}}
	if !reflect.DeepEqual(v, want) {
		t.Errorf("got %#v, want %#v", v, want)
	}
	for _, bad := range []string{`{"a":1,"a":2}`, `{"a":1} 2`, `[1e999]`, `{"a":}`} {
		if _, err := Parse([]byte(bad)); err == nil {
			t.Errorf("%s: want an error", bad)
		}
	}
}

// Compact is read back by a YAML 1.2 reader as the same value, whatever the
// strings hold.
func TestCompactRoundTripsThroughYAML(t *testing.T) {
	v := Object{
		{"on", "yes"}, {"n", []any{int64(-3), 0.5, 1e21, 1e-7}}, {"b", false}, {"nul", nil},
		{"s", "tab\tnl\ncr\rdel\u007fc1\u0085\u0090bom\ufeffls\u2028emoji😀 quote\" back\\ \u0001"},
		{"date", "2026-10-20"}, {"empty", Object{}}, {"arr", []any{}},
	}
	data, err := Compact(v)
	if err != nil {
		t.Fatal(err)
	}
	back, err := ParseYAML(data)
	if err != nil {
		t.Fatalf("%s: %v", data, err)
	}
	if !reflect.DeepEqual(back, v) {
		t.Errorf("YAML read\n%#v\nwant\n%#v", back, v)
	}
	if strings.ContainsAny(string(data), "\u007f\ufeff\u0085\u2028\u2029\t\n\r") {
		t.Errorf("Compact left a character YAML may not hold literally: %q", data)
	}
}
