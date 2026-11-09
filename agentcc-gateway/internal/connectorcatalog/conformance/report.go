package conformance

import (
	"fmt"
	"io"
	"sort"
	"strings"
)

// WriteText prints the report for a person: each case's verdict and rows,
// the skipped rows grouped by what they wait on, and one closing line with
// the counts. A run that passes with skips says how many in that line.
func (r *Report) WriteText(w io.Writer) {
	fmt.Fprintf(w, "L3 connector conformance, harness %s, run %s\n", r.Harness, r.RunAt)
	cases := map[string]int{}
	waits := map[string][]string{}
	for _, c := range r.Cases {
		cases[c.Verdict]++
		fmt.Fprintf(w, "\ncase %s (%s): %s\n", c.Case, c.Ref, strings.ToUpper(c.Verdict))
		for _, row := range c.Rows {
			switch row.Result {
			case ResultFail:
				fmt.Fprintf(w, "  FAIL  %s: %s\n", row.ID, row.Detail)
			case ResultSkip:
				fmt.Fprintf(w, "  skip  %s\n", row.ID)
				waits[row.Detail] = append(waits[row.Detail], c.Case+" "+row.ID)
			default:
				fmt.Fprintf(w, "  ok    %s\n", row.ID)
			}
		}
	}
	if len(waits) > 0 {
		fmt.Fprintf(w, "\nSkipped rows, by what each waits on:\n")
		reasons := make([]string, 0, len(waits))
		for reason := range waits {
			reasons = append(reasons, reason)
		}
		sort.Strings(reasons)
		for _, reason := range reasons {
			fmt.Fprintf(w, "- %s\n", reason)
			for _, at := range waits[reason] {
				fmt.Fprintf(w, "    %s\n", at)
			}
		}
	}
	for _, m := range r.Missing {
		fmt.Fprintf(w, "missing evidence: %s\n", m)
	}
	verdict := "PASS"
	if r.Failed() {
		verdict = "FAIL"
	}
	fmt.Fprintf(w, "\nL3 %s: %d cases (%d pass, %d fail, %d skipped whole); rows %d pass, %d fail, %d skipped with a named reason\n",
		verdict, len(r.Cases), cases[VerdictPass], cases[VerdictFail], cases[VerdictSkip], r.Pass, r.Fail, r.Skip)
}
