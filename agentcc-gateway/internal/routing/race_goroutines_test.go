package routing

import (
	"bytes"
	"regexp"
	"runtime"
	"testing"
	"testing/synctest"
)

var raceBubbleMarker = regexp.MustCompile(`synctest bubble [0-9]+`)

// NumGoroutine is process-wide even inside synctest: unrelated runtime/client
// activity can change it between assertions. The pinned Go toolchain includes
// each goroutine's bubble id in its stack header. Count ALL goroutines in this
// bubble (including context propagation and delayed cleanup), not just provider
// workers, and fail loudly if a future toolchain changes the header format.
func raceBubbleGoroutines(t *testing.T) int {
	t.Helper()
	own := make([]byte, 8192)
	n := runtime.Stack(own, false)
	marker := raceBubbleMarker.Find(bytes.SplitN(own[:n], []byte("\n"), 2)[0])
	if marker == nil {
		t.Fatal("pinned Go runtime omitted the synctest bubble identifier")
	}
	all := make([]byte, 64*1024)
	for {
		n = runtime.Stack(all, true)
		if n < len(all) {
			break
		}
		if len(all) >= 16*1024*1024 {
			t.Fatal("goroutine stack inventory exceeds test limit")
		}
		all = make([]byte, len(all)*2)
	}
	count := 0
	for _, stack := range bytes.Split(all[:n], []byte("\n\n")) {
		header := bytes.SplitN(stack, []byte("\n"), 2)[0]
		if bytes.Equal(raceBubbleMarker.Find(header), marker) {
			count++
		}
	}
	if count == 0 {
		t.Fatal("current bubble absent from stack inventory")
	}
	return count
}

func TestR602BubbleCounterDetectsOwnedWorkers(t *testing.T) {
	synctest.Test(t, func(t *testing.T) {
		before := raceBubbleGoroutines(t)
		done := make(chan struct{})
		go func() { <-done }()
		synctest.Wait()
		if got := raceBubbleGoroutines(t); got != before+1 {
			t.Fatalf("owned blocked worker not counted: before=%d after=%d", before, got)
		}
		close(done)
		synctest.Wait()
		if got := raceBubbleGoroutines(t); got != before {
			t.Fatalf("completed worker still counted: before=%d after=%d", before, got)
		}
	})
}
