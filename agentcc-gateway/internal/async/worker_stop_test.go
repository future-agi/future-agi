package async

import "testing"

// Stopping a worker twice stops it once: a server that stops its worker and
// is then shut down calls Stop again.
func TestWorker_StopTwiceStopsOnce(t *testing.T) {
	w := NewWorker(NewStore(), WorkerConfig{MaxWorkers: 1})
	w.Start()
	w.Stop()
	w.Stop()
	if apiErr := w.Submit(&Job{ID: "after-stop", Status: StatusQueued}); apiErr == nil {
		t.Fatal("a stopped worker accepted a job")
	}
}
