package observedcatalog

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kerr"
	"github.com/twmb/franz-go/pkg/kgo"
)

// Cancel at group release: the failed source must be discarded before the
// retry wait, not polled again at its already advanced cursor.
type interruptedSource struct {
	sourceStub
	cancel context.CancelFunc
	polls  int
}

func (s *interruptedSource) PollRecords(ctx context.Context, n int) kgo.Fetches {
	s.polls++
	return s.sourceStub.PollRecords(ctx, n)
}

func (s *interruptedSource) CloseAllowingRebalance() { s.cancel() }

func TestConsumerReleasesFailedSourceBeforeRetry(t *testing.T) {
	raw, err := Encode(testBatch(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name       string
		code       string
		commitErr  error
		wantRetry  bool
		wantCommit int
	}{
		{"quorum unavailable", "285", nil, true, 0},
		{"missing grant", "497", nil, false, 0},
		{"lost group generation", "", kerr.IllegalGeneration, true, 1},
		{"offset lost", "", kerr.OffsetOutOfRange, false, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			source := &interruptedSource{cancel: cancel, sourceStub: sourceStub{
				records: []*kgo.Record{{Topic: DefaultTopic, Value: raw}}, commitErr: tc.commitErr,
			}}
			var sink Sink = sinkFunc(func(context.Context, Batch) error { return nil })
			if tc.code != "" {
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					w.Header().Set("X-ClickHouse-Exception-Code", tc.code)
					w.WriteHeader(http.StatusInternalServerError)
				}))
				defer server.Close()
				sink, err = NewClickHouseSink(ClickHouseConfig{URL: server.URL, Database: "test"})
				if err != nil {
					t.Fatal(err)
				}
			}
			c := Consumer{source: source, sink: sink, cfg: KafkaConfig{Topic: DefaultTopic, Timeout: time.Second}}
			err := c.Run(ctx)
			if err == nil || errors.Is(err, context.Canceled) != tc.wantRetry || source.polls != 1 || source.commits != tc.wantCommit {
				t.Fatalf("Run: %v, polls=%d commit attempts=%d", err, source.polls, source.commits)
			}
		})
	}
}
