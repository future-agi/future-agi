// Standalone live candidate transport probe, built against the checkout's Go module.
// No lease guard, sequencer, activation, or delivery evidence is simulated here.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/future-agi/future-agi/fi-collector/pkg/catalogkafka"
	"github.com/future-agi/future-agi/fi-collector/pkg/propertycatalog"
	"github.com/twmb/franz-go/pkg/kgo"
)

type sourceRow struct {
	ProjectID string             `json:"project_id"`
	OrgID     string             `json:"org_id"`
	ID        string             `json:"id"`
	TraceID   string             `json:"trace_id"`
	StartTime string             `json:"start_time"`
	Model     string             `json:"model"`
	Strings   map[string]string  `json:"attrs_string"`
	Numbers   map[string]float64 `json:"attrs_number"`
	Booleans  map[string]uint8   `json:"attrs_bool"`
	Extra     string             `json:"attributes_extra"`
}

type input struct {
	Format         string      `json:"format"`
	Broker         string      `json:"broker"`
	Topic          string      `json:"topic"`
	OrganizationID string      `json:"organization_id"`
	WorkspaceID    string      `json:"workspace_id"`
	Rows           []sourceRow `json:"rows"`
	Output         string      `json:"output"`
}

type writer struct{ client *kgo.Client }

func (w *writer) WriteRecord(ctx context.Context, r catalogkafka.Record) error {
	return w.client.ProduceSync(ctx, &kgo.Record{Topic: r.Topic, Key: r.Key, Value: r.Value}).FirstErr()
}
func (w *writer) Close() { w.client.Close() }

func run(path string) error {
	path, err := filepath.Abs(path)
	if err != nil {
		return err
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	var in input
	if err := json.NewDecoder(io.LimitReader(file, 1<<20)).Decode(&in); err != nil {
		return err
	}
	host, _, err := net.SplitHostPort(in.Broker)
	if err != nil || host != "127.0.0.1" || in.Format != "futureagi.managed-smoke-input.v1" {
		return errors.New("requires generated loopback smoke input")
	}
	parent := filepath.Dir(path)
	runID := strings.TrimPrefix(filepath.Base(parent), "property-catalog-managed-")
	expectedTopic := "futureagi.managed." + runID + ".candidates.v2"
	expectedOutput := filepath.Join(parent, "kafka-evidence.json")
	if filepath.Base(path) == "application-live-input.json" {
		expectedTopic += ".application"
		expectedOutput = filepath.Join(parent, "application-live-kafka.json")
	}
	if len(runID) != 16 || in.Topic != expectedTopic || in.Output != expectedOutput {
		return errors.New("input does not belong to an isolated managed smoke directory")
	}
	if len(in.Rows) != 2 {
		return errors.New("fixture must contain two live scoped spans")
	}
	cfg := propertycatalog.RuntimeConfig{
		Mode: propertycatalog.RuntimeKafka, Environment: propertycatalog.DevelopmentEnvironment,
		DevelopmentAcknowledgement: propertycatalog.DevelopmentAcknowledgement,
		MaxSpansPerBatch:           8, MaxCandidateSpans: 8, MaxKeysPerSpan: 32,
		MaxArrayMembersPerSpan: 32, MaxEncodedBytesPerSpan: 16384,
		Kafka: propertycatalog.KafkaRuntimeConfig{Brokers: []string{in.Broker}, Topic: in.Topic},
	}.WithDefaults()
	if err := cfg.Validate(); err != nil {
		return err
	}
	var scoped []propertycatalog.ScopedSpan
	for _, row := range in.Rows {
		var extra map[string]any
		if err := json.Unmarshal([]byte(row.Extra), &extra); err != nil {
			return err
		}
		scoped = append(scoped, propertycatalog.ScopedSpan{
			OrganizationID: in.OrganizationID, WorkspaceID: in.WorkspaceID,
			Row: map[string]any{
				"org_id": row.OrgID, "project_id": row.ProjectID, "id": row.ID, "trace_id": row.TraceID,
				"start_time": row.StartTime, "model": row.Model, "attrs_string": row.Strings,
				"attrs_number": row.Numbers, "attrs_bool": row.Booleans, "attributes_extra": extra,
			},
		})
	}
	candidates, err := propertycatalog.BuildCandidates(cfg, scoped)
	if err != nil {
		return err
	}
	if len(candidates) != 1 {
		return errors.New("expected one candidate")
	}
	raw, err := candidates[0].MarshalBinary()
	if err != nil {
		return err
	}
	snapshot := candidates[0].Snapshot()
	if snapshot.Version != 2 || snapshot.CatalogEpoch != 0 || snapshot.ProjectionVersion != 0 || len(snapshot.GapReasons) != 0 {
		return errors.New("managed candidate must be complete and allocation-free")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
	defer cancel()
	client, err := kgo.NewClient(kgo.SeedBrokers(in.Broker), kgo.RequiredAcks(kgo.AllISRAcks()), kgo.MaxBufferedRecords(1), kgo.MaxBufferedBytes(1<<20), kgo.RecordDeliveryTimeout(10*time.Second))
	if err != nil {
		return err
	}
	producer, err := propertycatalog.NewCandidateProducer(in.Topic, &writer{client})
	if err != nil {
		client.Close()
		return err
	}
	defer producer.Close()
	for n := 0; n < 2; n++ {
		if err := producer.Publish(ctx, candidates[0]); err != nil {
			return err
		}
	}
	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(in.Broker),
		kgo.ConsumePartitions(map[string]map[int32]kgo.Offset{in.Topic: {0: kgo.NewOffset().AtStart()}}),
		kgo.FetchIsolationLevel(kgo.ReadCommitted()), kgo.FetchMaxBytes(1<<20),
		kgo.FetchMaxPartitionBytes(1<<20), kgo.MaxConcurrentFetches(1),
	)
	if err != nil {
		return err
	}
	defer consumer.Close()
	count := 0
	var offsets []int64
	for count < 2 {
		fetched := consumer.PollRecords(ctx, 2-count)
		if err := fetched.Err(); err != nil {
			return err
		}
		for _, record := range fetched.Records() {
			if !bytes.Equal(record.Value, raw) || string(record.Key) != in.WorkspaceID {
				return errors.New("Kafka changed candidate bytes or tenant key")
			}
			parsed, err := propertycatalog.ParseWireCandidate(record.Value)
			if err != nil {
				return err
			}
			if parsed.Snapshot().CandidateID != snapshot.CandidateID {
				return errors.New("duplicate candidate identity changed")
			}
			offsets = append(offsets, record.Offset)
			count++
		}
	}
	if len(offsets) != 2 || offsets[0] != 0 || offsets[1] != 1 {
		return errors.New("topic was not empty or offsets did not advance")
	}
	result := map[string]any{
		"roundtrip_records": count, "offsets": offsets, "duplicate_candidate_id_equal": true,
		"candidates": []json.RawMessage{raw}, "lifecycle_qualified": false,
	}
	output, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(in.Output, append(output, '\n'), 0600); err != nil {
		return err
	}
	fmt.Printf("Live Kafka: %d records, identical managed candidate IDs, offsets %v\n", count, offsets)
	return nil
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: candidate-smoke <generated candidate-input.json>")
		os.Exit(64)
	}
	if err := run(os.Args[1]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
