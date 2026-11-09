package metrics

import (
	"strings"
	"testing"
)

// TestCollectorRendersAtScrape: a collector's families are read each render,
// in exposition format with escaped labels; setting one under a name twice
// renders it once.
func TestCollectorRendersAtScrape(t *testing.T) {
	r := NewRegistry()
	n := 0.0
	collect := func() []Family {
		n++
		return []Family{
			{Name: "x_gauge", Help: "a gauge", Type: "gauge", Samples: []Sample{{Labels: map[string]string{"provider": `a"b`}, Value: n}}},
			{Name: "x_hist", Help: "a histogram", Type: "histogram", Samples: []Sample{
				{Suffix: "_bucket", Labels: map[string]string{"provider": "p", "le": "+Inf"}, Value: 2},
				{Suffix: "_sum", Labels: map[string]string{"provider": "p"}, Value: 1.5},
				{Suffix: "_count", Labels: map[string]string{"provider": "p"}, Value: 2},
			}},
		}
	}
	r.SetCollector("x", collect)
	r.SetCollector("x", collect)
	out := r.Render()
	for _, want := range []string{
		"# TYPE x_gauge gauge\n", `x_gauge{provider="a\"b"} 1` + "\n",
		"# TYPE x_hist histogram\n", `x_hist_bucket{le="+Inf",provider="p"} 2` + "\n",
		`x_hist_sum{provider="p"} 1.5` + "\n", `x_hist_count{provider="p"} 2` + "\n",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("render lacks %q:\n%s", want, out)
		}
	}
	if strings.Count(out, "# TYPE x_gauge") != 1 {
		t.Errorf("a collector set twice rendered twice:\n%s", out)
	}
	if out = r.Render(); !strings.Contains(out, `x_gauge{provider="a\"b"} 2`) {
		t.Errorf("the second render did not read the collector again:\n%s", out)
	}
}
