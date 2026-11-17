package server

import (
	"context"
	"testing"
)

// A server whose async worker was stopped shuts down without stopping it a
// second time into a panic.
func TestShutdownAfterTheAsyncWorkerWasStopped(t *testing.T) {
	mock := startMockOpenAI(t)
	defer mock.Close()
	s := createTestServer(t, mock.URL)
	s.asyncWorker.Stop()
	s.asyncWorker.Stop()
	if err := s.Shutdown(context.Background()); err != nil {
		t.Fatalf("shutdown: %v", err)
	}
}
