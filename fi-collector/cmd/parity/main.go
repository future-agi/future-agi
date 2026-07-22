// parity harness: read JSONL attribute dicts on stdin, run them through the
// SAME adapter.Split the fi-collector uses, emit the typed maps as JSON.
// Paired with the Python pg_to_ch_adapter.split_attributes to prove the two
// ports agree on real data. (Caveat: JSON numbers decode to float64 here, so
// the int-vs-float distinction the live OTLP path preserves is flattened —
// big-int routing is covered by unit tests, not this harness.)
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"

	"github.com/future-agi/future-agi/fi-collector/pkg/adapter"
	"go.opentelemetry.io/collector/pdata/pcommon"
)

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	for sc.Scan() {
		line := sc.Bytes()
		if len(line) == 0 {
			continue
		}
		var raw map[string]any
		if err := json.Unmarshal(line, &raw); err != nil {
			fmt.Println(`{"_err":"json"}`)
			continue
		}
		m := pcommon.NewMap()
		_ = m.FromRaw(raw)
		s := map[string]string{}
		n := map[string]float64{}
		b := map[string]uint8{}
		o := map[string]any{}
		adapter.Split(m, s, n, b, o)
		out, _ := json.Marshal(map[string]any{"str": s, "num": n, "bool": b, "overflow": o})
		fmt.Println(string(out))
	}
}
