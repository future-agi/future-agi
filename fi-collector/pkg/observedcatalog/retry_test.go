package observedcatalog

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
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
		// Topology resolves first, so 285 comes from the INSERT itself.
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
					if tc.wantRetry && strings.HasPrefix(r.URL.Query().Get("query"), "SELECT") {
						_, _ = w.Write([]byte(`{"engine":"AggregatingMergeTree"}` + "\n"))
						return
					}
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

// A replacement member that never returns: optionally it reports a completed
// (empty) group assignment first, as a healthy idle member does.
type stalledSource struct {
	sourceStub
	onJoined func()
	polls    atomic.Int32
}

func (s *stalledSource) PollRecords(ctx context.Context, _ int) kgo.Fetches {
	s.polls.Add(1)
	if s.onJoined != nil {
		s.onJoined()
	}
	<-ctx.Done()
	return kgo.NewErrFetch(ctx.Err())
}

func TestConsumerBoundsRecoveryPollByRetryWindow(t *testing.T) {
	raw, err := Encode(testBatch(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name   string
		joined bool
	}{
		{"rejoin never completes", false},
		{"rejoined and idle", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
			defer cancel()
			replacement := &stalledSource{}
			c := Consumer{
				source: &sourceStub{records: []*kgo.Record{{Topic: DefaultTopic, Value: raw}}, commitErr: kerr.IllegalGeneration},
				sink:   sinkFunc(func(context.Context, Batch) error { return nil }),
				cfg:    KafkaConfig{Topic: DefaultTopic, Timeout: time.Second},
				newSource: func(_ KafkaConfig, onJoined func()) (RecordSource, error) {
					if tc.joined {
						replacement.onJoined = onJoined
					}
					return replacement, nil
				},
				retryCeiling: 2500 * time.Millisecond,
			}
			start := time.Now()
			err := c.Run(ctx)
			elapsed := time.Since(start)
			if tc.joined {
				// Recovered: the window no longer applies, so only shutdown ends Run.
				if !errors.Is(err, context.DeadlineExceeded) || replacement.polls.Load() < 2 {
					t.Fatalf("Run: %v after %s, polls=%d", err, elapsed, replacement.polls.Load())
				}
				return
			}
			if !errors.Is(err, errRecoveryPoll) || !strings.Contains(err.Error(), "retry window expired") ||
				ctx.Err() != nil || elapsed > 4*time.Second || replacement.polls.Load() != 1 {
				t.Fatalf("Run: %v after %s, polls=%d", err, elapsed, replacement.polls.Load())
			}
		})
	}
}
