package ingestbench

import (
	"errors"
	"io"
	"strings"
	"testing"
)

type reportLimitWriter struct {
	remaining int
	err       error
	calls     int
}

func (w *reportLimitWriter) Write(p []byte) (int, error) {
	w.calls++
	n := min(len(p), w.remaining)
	w.remaining -= n
	if n < len(p) {
		return n, w.err
	}
	return n, nil
}

func TestReportPropagatesOutputErrors(t *testing.T) {
	writeErr := errors.New("report output unavailable")
	for _, tc := range []struct {
		name      string
		remaining int
		writeErr  error
		want      error
	}{
		{"first-write", 0, writeErr, writeErr},
		{"after-partial-output", 4096, writeErr, writeErr},
		{"short-write", 0, nil, io.ErrShortWrite},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := repairReceipt()
			r.Warnings = append(r.Warnings, strings.Repeat("x", 8192))
			w := &reportLimitWriter{remaining: tc.remaining, err: tc.writeErr}
			if err := Report(w, r); !errors.Is(err, tc.want) {
				t.Fatalf("Report error = %v, want %v", err, tc.want)
			}
			if w.calls == 0 || w.remaining != 0 {
				t.Fatalf("output failure not exercised: %+v", w)
			}
		})
	}
}

func TestReportRejectsInvalidReceiptBeforeWriting(t *testing.T) {
	r := repairReceipt()
	r.PlannedSpans++
	w := &reportLimitWriter{err: errors.New("output unavailable")}
	if err := Report(w, r); err == nil {
		t.Fatal("invalid receipt accepted")
	}
	if w.calls != 0 {
		t.Fatal("invalid receipt reached output")
	}
}
