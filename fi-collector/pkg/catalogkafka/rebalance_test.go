package catalogkafka

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync"
	"testing"

	"github.com/twmb/franz-go/pkg/kgo"
)

type scriptedCheckpointLoader struct {
	mu        sync.Mutex
	loads     int
	responses [][]StreamCheckpoint
	errors    []error
}

func (loader *scriptedCheckpointLoader) Load(context.Context) ([]StreamCheckpoint, error) {
	loader.mu.Lock()
	defer loader.mu.Unlock()
	index := loader.loads
	loader.loads++
	if index < len(loader.errors) && loader.errors[index] != nil {
		return nil, loader.errors[index]
	}
	if index >= len(loader.responses) {
		return nil, nil
	}
	return append([]StreamCheckpoint(nil), loader.responses[index]...), nil
}

func (loader *scriptedCheckpointLoader) LoadCount() int {
	loader.mu.Lock()
	defer loader.mu.Unlock()
	return loader.loads
}

func checkpointFor(sequence uint64, payloadCharacter, envelopeCharacter string) StreamCheckpoint {
	return StreamCheckpoint{
		ProjectID: testProject, CatalogEpoch: 1, ProducerStreamID: testStream,
		Sequence: sequence, PayloadSHA256: strings.Repeat(payloadCharacter, 64),
		EnvelopeID: strings.Repeat(envelopeCharacter, 64),
	}
}

func TestMergeCheckpointsIsMonotonicAndAtomic(t *testing.T) {
	validator, err := NewSequenceValidator([]StreamCheckpoint{checkpointFor(3, "3", "a")})
	if err != nil {
		t.Fatal(err)
	}

	// A lagging replica must not regress in-process state, even if the older
	// checkpoint carries a different identity.
	if err := validator.MergeCheckpoints([]StreamCheckpoint{checkpointFor(2, "2", "b")}); err != nil {
		t.Fatal(err)
	}
	checkpoint, ok := validator.Checkpoint(testProject, 1, testStream)
	if !ok || checkpoint.Sequence != 3 || checkpoint.PayloadSHA256 != strings.Repeat("3", 64) {
		t.Fatalf("checkpoint regressed: %+v present=%v", checkpoint, ok)
	}

	secondStream := StreamCheckpoint{
		ProjectID: testProject, CatalogEpoch: 1,
		ProducerStreamID: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
		Sequence:         1, PayloadSHA256: strings.Repeat("4", 64), EnvelopeID: strings.Repeat("c", 64),
	}
	conflict := checkpointFor(3, "f", "e")
	if err := validator.MergeCheckpoints([]StreamCheckpoint{secondStream, conflict}); !errors.Is(err, ErrCheckpointConflict) {
		t.Fatalf("same-sequence conflict=%v", err)
	}
	if _, exists := validator.Checkpoint(
		secondStream.ProjectID, secondStream.CatalogEpoch, secondStream.ProducerStreamID,
	); exists {
		t.Fatal("failed atomic merge partially installed a different stream")
	}

	advanced := checkpointFor(5, "5", "d")
	if err := validator.MergeCheckpoints([]StreamCheckpoint{advanced, secondStream}); err != nil {
		t.Fatal(err)
	}
	checkpoint, _ = validator.Checkpoint(testProject, 1, testStream)
	if checkpoint != advanced {
		t.Fatalf("checkpoint=%+v want=%+v", checkpoint, advanced)
	}
}

func TestMergeCheckpointsInvalidatesOutstandingValidation(t *testing.T) {
	first := mustEnvelope(t, testEnvelopeInput(t))
	validator, err := NewSequenceValidator([]StreamCheckpoint{{
		ProjectID: first.ProjectID(), CatalogEpoch: first.CatalogEpoch(),
		ProducerStreamID: first.ProducerStreamID(), Sequence: first.Sequence(),
		PayloadSHA256: first.PayloadSHA256(), EnvelopeID: first.EnvelopeID(),
	}})
	if err != nil {
		t.Fatal(err)
	}
	secondInput := testEnvelopeInput(t)
	secondInput.Sequence = 2
	secondInput.PreviousPayloadSHA256 = first.PayloadSHA256()
	second := mustEnvelope(t, secondInput)
	validation, err := validator.Check(second)
	if err != nil {
		t.Fatal(err)
	}
	if err := validator.MergeCheckpoints([]StreamCheckpoint{{
		ProjectID: second.ProjectID(), CatalogEpoch: second.CatalogEpoch(),
		ProducerStreamID: second.ProducerStreamID(), Sequence: second.Sequence(),
		PayloadSHA256: second.PayloadSHA256(), EnvelopeID: second.EnvelopeID(),
	}}); err != nil {
		t.Fatal(err)
	}
	if err := validator.Acknowledge(validation); !errors.Is(err, ErrSequenceRace) {
		t.Fatalf("stale validation acknowledge=%v", err)
	}
}

func TestAssignmentRefreshRunsEveryTimeAndPreservesOffsets(t *testing.T) {
	first := checkpointFor(1, "1", "a")
	second := checkpointFor(2, "2", "b")
	loader := &scriptedCheckpointLoader{responses: [][]StreamCheckpoint{{first}, {second}}}
	validator, _ := NewSequenceValidator(nil)
	failure := &stickyConsumerError{}
	cfg := FranzConsumerConfig{
		Brokers: []string{"127.0.0.1:1"}, Topic: "catalog-v3", GroupID: "catalog-test",
		AssignmentCheckpointLoader: loader,
	}
	client, err := kgo.NewClient(franzConsumerRuntimeOptions(cfg, validator, failure)...)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseAllowingRebalance()
	adjust, ok := client.OptValue(kgo.AdjustFetchOffsetsFn).(func(
		context.Context, map[string]map[int32]kgo.Offset,
	) (map[string]map[int32]kgo.Offset, error))
	if !ok {
		t.Fatalf("adjust callback type=%T", client.OptValue(kgo.AdjustFetchOffsetsFn))
	}
	offsets := map[string]map[int32]kgo.Offset{
		"catalog-v3": {2: kgo.NewOffset().At(41)},
	}
	for assignment := 0; assignment < 2; assignment++ {
		got, err := adjust(context.Background(), offsets)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(got, offsets) {
			t.Fatalf("assignment changed Kafka offsets: got=%v want=%v", got, offsets)
		}
	}
	if loader.LoadCount() != 2 {
		t.Fatalf("ledger loads=%d want=2", loader.LoadCount())
	}
	checkpoint, ok := validator.Checkpoint(testProject, 1, testStream)
	if !ok || checkpoint != second {
		t.Fatalf("refreshed checkpoint=%+v present=%v", checkpoint, ok)
	}
}

func TestFranzAssignmentRefreshFailureIsStickyAndPreventsCommit(t *testing.T) {
	readFailure := errors.New("ledger unavailable")
	loader := &scriptedCheckpointLoader{
		responses: [][]StreamCheckpoint{{checkpointFor(1, "1", "a")}},
		errors:    []error{readFailure},
	}
	validator, _ := NewSequenceValidator(nil)
	failure := &stickyConsumerError{}
	cfg := FranzConsumerConfig{
		Brokers: []string{"127.0.0.1:1"}, Topic: "catalog-v3", GroupID: "catalog-test",
		AssignmentCheckpointLoader: loader,
	}
	client, err := kgo.NewClient(franzConsumerRuntimeOptions(cfg, validator, failure)...)
	if err != nil {
		t.Fatal(err)
	}
	defer client.CloseAllowingRebalance()
	adjust, ok := client.OptValue(kgo.AdjustFetchOffsetsFn).(func(
		context.Context, map[string]map[int32]kgo.Offset,
	) (map[string]map[int32]kgo.Offset, error))
	if !ok {
		t.Fatalf("adjust callback type=%T", client.OptValue(kgo.AdjustFetchOffsetsFn))
	}
	offsets := map[string]map[int32]kgo.Offset{"catalog-v3": {0: kgo.NewOffset().At(9)}}
	if _, err := adjust(context.Background(), offsets); !errors.Is(err, readFailure) ||
		!errors.Is(err, ErrAssignmentCheckpointRefresh) {
		t.Fatalf("first assignment error=%v", err)
	}
	firstSticky := failure.Err()
	if _, err := adjust(context.Background(), offsets); err != firstSticky {
		t.Fatalf("second assignment error=%v want same sticky error %v", err, firstSticky)
	}
	if loader.LoadCount() != 1 {
		t.Fatalf("sticky failure retried ledger load %d times", loader.LoadCount())
	}

	source := &franzSource{client: client, failure: failure}
	if err := source.Commit(context.Background(), Record{
		Topic: "catalog-v3", Partition: 0, Offset: 9,
	}); err != firstSticky {
		t.Fatalf("commit after assignment failure=%v want=%v", err, firstSticky)
	}
	if _, exists := validator.Checkpoint(testProject, 1, testStream); exists {
		t.Fatal("failed assignment advanced validator")
	}
}
