package batch

import (
	"testing"
	"time"
)

// Stop may run more than once (a server's Shutdown and a caller's own
// cleanup can both stop the processor), and the retention loop has returned
// once Done is closed.
func TestProcessorStopIsIdempotentAndEndsTheRetentionLoop(t *testing.T) {
	p := NewProcessor(nil)
	p.Stop()
	p.Stop()
	select {
	case <-p.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("the retention loop is still running after Stop")
	}
}
