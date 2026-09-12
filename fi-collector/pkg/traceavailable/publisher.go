// Package traceavailable announces stored, ended roots for scheduled Error Feed
// investigations. Notifications are hints; canonical reconciliation covers gaps.
package traceavailable

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/twmb/franz-go/pkg/kgo"
)

const Topic = "error-feed.trace-available.v1"
const maxTraces = 100
const maxBytes = 64 << 10

type Root struct {
	OrganizationID string
	WorkspaceID    string
	ProjectID      string
	TraceID        string
	SpanID         string
	EndTime        time.Time
}

type Trace struct {
	TraceID     string    `json:"trace_id"`
	RootSpanID  string    `json:"root_span_id"`
	RootEndTime time.Time `json:"root_end_time"`
}

type Event struct {
	Version        int       `json:"version"`
	EventID        string    `json:"event_id"`
	OrganizationID string    `json:"organization_id"`
	WorkspaceID    *string   `json:"workspace_id"`
	ProjectID      string    `json:"project_id"`
	EventKind      string    `json:"event_kind"`
	Traces         []Trace   `json:"traces"`
	EmittedAt      time.Time `json:"emitted_at"`
}

// ExtractRoots uses authenticated project scope and copies only small metadata.
// The caller must wait for the corresponding canonical insert before enqueueing.
func ExtractRoots(rows []map[string]any, org, workspace string, projects map[string]struct{}) []Root {
	if _, err := uuid.Parse(org); err != nil {
		return nil
	}
	roots := make([]Root, 0)
	for _, row := range rows {
		parent, ok := row["parent_span_id"].(string)
		if !ok || parent != "" {
			continue
		}
		project, _ := row["project_id"].(string)
		if _, allowed := projects[project]; !allowed {
			continue
		}
		trace, _ := row["trace_id"].(string)
		span, _ := row["id"].(string)
		ended, _ := row["end_time"].(string)
		end, err := time.ParseInLocation("2006-01-02 15:04:05.999999", ended, time.UTC)
		if err != nil || span == "" || end.Unix() <= 0 {
			continue
		}
		if parsed, err := uuid.Parse(trace); err != nil || parsed == uuid.Nil {
			continue
		}
		roots = append(roots, Root{org, workspace, project, trace, span, end})
	}
	return roots
}

func BuildEvents(roots []Root, now time.Time) ([]Event, error) {
	groups := map[string][]Root{}
	for _, root := range roots {
		for _, id := range []string{root.OrganizationID, root.ProjectID, root.TraceID} {
			parsed, err := uuid.Parse(id)
			if err != nil || parsed == uuid.Nil {
				return nil, errors.New("trace notification has invalid scope or trace ID")
			}
		}
		if root.WorkspaceID != "" {
			if _, err := uuid.Parse(root.WorkspaceID); err != nil {
				return nil, errors.New("invalid workspace")
			}
		}
		if root.SpanID == "" || len(root.SpanID) > 64 || root.EndTime.IsZero() {
			return nil, errors.New("invalid ended root")
		}
		key := root.OrganizationID + "/" + root.WorkspaceID + "/" + root.ProjectID
		groups[key] = append(groups[key], root)
	}
	keys := make([]string, 0, len(groups))
	for key := range groups {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	events := make([]Event, 0)
	for _, key := range keys {
		group := groups[key]
		seen := map[string]bool{}
		var event Event
		for _, root := range group {
			identity := root.TraceID + "/" + root.SpanID
			if seen[identity] {
				continue
			}
			seen[identity] = true
			if len(event.Traces) == 0 {
				event = Event{Version: 1, EventID: uuid.NewString(), OrganizationID: root.OrganizationID,
					ProjectID: root.ProjectID, EventKind: "root_span_written", EmittedAt: now.UTC()}
				if root.WorkspaceID != "" {
					workspace := root.WorkspaceID
					event.WorkspaceID = &workspace
				}
			}
			event.Traces = append(event.Traces, Trace{root.TraceID, root.SpanID, root.EndTime.UTC()})
			raw, err := json.Marshal(event)
			if err != nil || len(raw) > maxBytes {
				return nil, errors.New("trace notification exceeds byte limit")
			}
			if len(event.Traces) == maxTraces {
				events = append(events, event)
				event = Event{}
			}
		}
		if len(event.Traces) > 0 {
			events = append(events, event)
		}
	}
	return events, nil
}

type Publisher struct {
	client *kgo.Client
	topic  string
	queue  chan Event
	done   chan struct{}
	cancel context.CancelFunc
	mu     sync.Mutex
	closed bool
	log    *slog.Logger
}

// FromEnv is disabled unless explicitly enabled. The first transport matches
// the existing local Kafka broker. Authenticated production transport must be
// configured before exposing this path beyond that trusted network.
func FromEnv(log *slog.Logger) (*Publisher, error) {
	enabled := os.Getenv("FI_ERROR_FEED_ENABLED")
	if enabled == "" || enabled == "false" {
		return nil, nil
	}
	if enabled != "true" {
		return nil, errors.New("FI_ERROR_FEED_ENABLED must be true or false")
	}
	brokers := strings.Split(os.Getenv("FI_ERROR_FEED_KAFKA_BROKERS"), ",")
	for i, b := range brokers {
		brokers[i] = strings.TrimSpace(b)
		if brokers[i] == "" {
			return nil, errors.New("FI_ERROR_FEED_KAFKA_BROKERS required")
		}
	}
	topic := os.Getenv("FI_ERROR_FEED_KAFKA_TOPIC")
	if topic == "" {
		topic = Topic
	}
	client, err := kgo.NewClient(kgo.SeedBrokers(brokers...), kgo.ClientID("fi-error-feed-roots"),
		kgo.RequiredAcks(kgo.AllISRAcks()), kgo.RecordDeliveryTimeout(10*time.Second),
		kgo.MaxBufferedRecords(100), kgo.MaxBufferedBytes(1<<20))
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	p := &Publisher{client: client, topic: topic, queue: make(chan Event, 64), done: make(chan struct{}), cancel: cancel, log: log}
	go p.run(ctx)
	return p, nil
}

func (p *Publisher) EnqueueRoots(roots []Root) error {
	events, err := BuildEvents(roots, time.Now())
	if err != nil || len(events) == 0 {
		return err
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return errors.New("trace notification publisher closed")
	}
	for _, event := range events {
		select {
		case p.queue <- event:
		default:
			return errors.New("trace notification queue full; remaining roots require reconciliation")
		}
	}
	return nil
}

func (p *Publisher) run(ctx context.Context) {
	defer close(p.done)
	defer p.client.Close()
	for event := range p.queue {
		batchCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		raw, _ := json.Marshal(event)
		workspace := ""
		if event.WorkspaceID != nil {
			workspace = *event.WorkspaceID
		}
		key := event.OrganizationID + "/" + workspace + "/" + event.ProjectID
		err := p.client.ProduceSync(batchCtx, &kgo.Record{Topic: p.topic, Key: []byte(key), Value: raw}).FirstErr()
		if err != nil {
			p.log.Warn("error feed notification gap; reconciliation required", "event_id", event.EventID, "error", err)
		}
		cancel()
		if ctx.Err() != nil {
			return
		}
	}
}

func (p *Publisher) Shutdown(ctx context.Context) error {
	p.mu.Lock()
	if !p.closed {
		p.closed = true
		close(p.queue)
	}
	p.mu.Unlock()
	select {
	case <-p.done:
		p.cancel()
		return nil
	case <-ctx.Done():
		p.cancel()
		return fmt.Errorf("trace notification drain: %w", ctx.Err())
	}
}
