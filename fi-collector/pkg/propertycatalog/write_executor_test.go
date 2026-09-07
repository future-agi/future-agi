package propertycatalog

import (
	"context"
	"errors"
	"io"
	"testing"
	"time"
)

type explicitWriteProof struct {
	attests   int
	covers    int
	resolves  int
	attestErr error
	coverErr  error
	outcome   string
	foreign   bool
	onCover   func(ExactWriteAttempt)
}

func (p *explicitWriteProof) Attest(context.Context, *WritePolicy) error {
	p.attests++
	return p.attestErr
}
func (p *explicitWriteProof) Cover(_ context.Context, _ *WritePolicy, a ExactWriteAttempt) error {
	p.covers++
	if p.onCover != nil {
		p.onCover(a)
	}
	return p.coverErr
}
func (p *explicitWriteProof) Resolve(_ context.Context, _ *WritePolicy, a ExactWriteAttempt) (AttemptSettlement, error) {
	p.resolves++
	result := AttemptSettlement{
		Outcome: p.outcome, QueryID: a.QueryID, BodySHA256: a.BodySHA256,
		TopologySHA256: a.TopologySHA256, WitnessSHA256: testDigest("explicit reviewed witness"),
	}
	if p.foreign {
		result.BodySHA256 = testDigest("other payload")
	}
	return result, nil
}

func executorFixture(t *testing.T, directory string, n int, proof CatalogWriteProof, send ExactInsertSender) (*DurableWriteExecutor, ExactWriteAttempt) {
	t.Helper()
	policy, err := NewWritePolicy(testWriteAdmission(t, n, false))
	if err != nil {
		t.Fatal(err)
	}
	journal, err := OpenWriteAttemptJournal(directory)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { journal.Close() })
	executor, err := NewDurableWriteExecutor(policy, journal, proof, send)
	if err != nil {
		t.Fatal(err)
	}
	intent := testAttemptIntent()
	intent.TopologySHA256 = policy.admission.TopologySHA256
	return executor, intent
}

func boundedWriteTestContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	t.Cleanup(cancel)
	return ctx
}

func TestWriteExecutorDurableSentBeforeIOAndAllReplicaProofBeforeCompletion(t *testing.T) {
	for _, n := range []int{1, 2, 4} {
		proof := &explicitWriteProof{}
		var executor *DurableWriteExecutor
		sends := 0
		send := func(_ context.Context, a ExactWriteAttempt) error {
			sends++
			if a.State != AttemptSent || a.QueryID == "" || a.BodySHA256 != sha256Hex(a.Body) {
				t.Fatal("transport saw an unprepared attempt")
			}
			if lease, err := executor.journal.Lock(a.Token); err == nil {
				lease.Close()
				t.Fatal("attempt lock was released while a writer was in flight")
			}
			return nil
		}
		var intent ExactWriteAttempt
		executor, intent = executorFixture(t, t.TempDir(), n, proof, send)
		proof.onCover = func(a ExactWriteAttempt) {
			if a.State != AttemptAcknowledged && a.State != AttemptComplete {
				t.Fatalf("coverage attempted without settled write: %s", a.State)
			}
		}
		ctx := boundedWriteTestContext(t)
		if err := executor.Execute(ctx, intent, false); err != nil {
			t.Fatal(err)
		}
		if err := executor.Execute(ctx, intent, true); err != nil {
			t.Fatal(err)
		}
		if sends != 1 || proof.covers != 2 || proof.attests != 4 || proof.resolves != 0 {
			t.Fatalf("sends=%d proof=%+v", sends, proof)
		}
	}
}

func TestWriteExecutorLostAckRestartResolvesReadOnlyAndNeverResends(t *testing.T) {
	directory := t.TempDir()
	proof := &explicitWriteProof{outcome: "unresolved"}
	sends := 0
	send := func(context.Context, ExactWriteAttempt) error { sends++; return io.ErrUnexpectedEOF }
	first, intent := executorFixture(t, directory, 3, proof, send)
	if err := first.Execute(boundedWriteTestContext(t), intent, false); !errors.Is(err, ErrWriteUnresolved) {
		t.Fatalf("lost ACK error=%v", err)
	}
	_ = first.journal.Close()
	restarted, _ := executorFixture(t, directory, 3, proof, send)
	for _, outcome := range []string{"", "unresolved", "settled_abort"} {
		proof.outcome = outcome
		if err := restarted.Execute(boundedWriteTestContext(t), intent, false); !errors.Is(err, ErrWriteUnresolved) {
			t.Fatalf("%q allowed completion: %v", outcome, err)
		}
	}
	proof.outcome, proof.foreign = "settled_success", true
	if err := restarted.Execute(boundedWriteTestContext(t), intent, false); err == nil {
		t.Fatal("foreign settlement accepted")
	}
	proof.foreign = false
	if err := restarted.Execute(boundedWriteTestContext(t), intent, false); err != nil {
		t.Fatal(err)
	}
	if sends != 1 || proof.covers != 1 {
		t.Fatalf("restart replayed INSERT or covered unresolved write: sends=%d covers=%d", sends, proof.covers)
	}
}

func TestWriteExecutorProofFailureIsRecoverableAfterAckWithoutWriteReplay(t *testing.T) {
	proof := &explicitWriteProof{coverErr: errors.New("replica two lagging")}
	sends := 0
	executor, intent := executorFixture(t, t.TempDir(), 2, proof, func(context.Context, ExactWriteAttempt) error { sends++; return nil })
	if err := executor.Execute(boundedWriteTestContext(t), intent, false); err == nil {
		t.Fatal("local ACK bypassed all-replica coverage")
	}
	proof.coverErr = nil
	if err := executor.Execute(boundedWriteTestContext(t), intent, false); err != nil {
		t.Fatal(err)
	}
	proof.attestErr = errors.New("membership changed")
	if err := executor.Execute(boundedWriteTestContext(t), intent, true); err == nil {
		t.Fatal("complete receipt bypassed changed topology")
	}
	if sends != 1 || proof.resolves != 0 {
		t.Fatalf("ACK receipt lost: sends=%d resolves=%d", sends, proof.resolves)
	}
}

type executorTestHandler struct {
	executor *DurableWriteExecutor
	intent   ExactWriteAttempt
}

func (h executorTestHandler) Deliver(ctx context.Context, delivery Delivery) error {
	ctx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	return h.executor.Execute(ctx, h.intent, delivery.ExactDuplicate)
}

// This exercises the real Consumer commit boundary with the new executor. The
// production DeliveryHandler/HTTP factory wiring is a separate integration gate.
func TestWriteExecutorConsumerNeverCommitsAmbiguityOrUnprovenDuplicate(t *testing.T) {
	proof := &explicitWriteProof{outcome: "unresolved"}
	sends := 0
	executor, intent := executorFixture(t, t.TempDir(), 2, proof, func(context.Context, ExactWriteAttempt) error { sends++; return io.ErrUnexpectedEOF })
	envelope := mustEnvelope(t, definitionDeliveryInput(t, 1))
	intent.EnvelopeID = envelope.EnvelopeID()
	intent.Token = "property-catalog-v1:" + intent.EnvelopeID + ":chunk:0"
	source := &oneRecordSource{record: kafkaRecord(t, envelope, 5)}
	validator, _ := NewSequenceValidator(nil)
	consumer, err := NewConsumer("property-catalog", source, executorTestHandler{executor, intent}, validator)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err := consumer.ProcessOne(context.Background()); !errors.Is(err, ErrWriteUnresolved) || source.commits != 0 {
			t.Fatalf("ambiguous write committed: err=%v commits=%d", err, source.commits)
		}
	}
	proof.outcome = "settled_success"
	if err := consumer.ProcessOne(context.Background()); err != nil || source.commits != 1 {
		t.Fatalf("settled proof did not permit commit: err=%v commits=%d", err, source.commits)
	}
	proof.coverErr = errors.New("replica unavailable during duplicate")
	if err := consumer.ProcessOne(context.Background()); err == nil || source.commits != 1 || sends != 1 {
		t.Fatalf("duplicate bypass: err=%v commits=%d sends=%d", err, source.commits, sends)
	}
}

func TestWriteExecutorRequiresExplicitProofAndMissingDuplicateNeverSends(t *testing.T) {
	if _, err := NewDurableWriteExecutor(nil, nil, nil, nil); err == nil {
		t.Fatal("missing proof defaults to success")
	}
	executor, intent := executorFixture(t, t.TempDir(), 1, &explicitWriteProof{}, func(context.Context, ExactWriteAttempt) error {
		t.Fatal("unproven duplicate sent an INSERT")
		return nil
	})
	if err := executor.Execute(boundedWriteTestContext(t), intent, true); !errors.Is(err, ErrWriteUnresolved) {
		t.Fatalf("missing duplicate receipt error=%v", err)
	}
}
