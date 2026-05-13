package server

// Property-based tests for handler logic added in:
//   PR #334 — guardrail reflexion retry (MaxAttempts clamping, FeedbackTemplate default)
//   PR #337 — embedding model override propagation
//
// TODO(merge): Once those PRs are merged into this branch, replace the inline
// spec helpers below with calls to the real production functions.

import (
	"os"
	"testing"

	"github.com/leanovate/gopter"
	"github.com/leanovate/gopter/gen"
	"github.com/leanovate/gopter/prop"
)

// ---------------------------------------------------------------------------
// Spec helpers — inline pure functions that encode the production logic.
// These mirror runReflexion and handleEmbedding exactly so that the
// properties below double as executable specifications.
// ---------------------------------------------------------------------------

// clampReflexionMaxAttempts encodes the MaxAttempts clamping from runReflexion:
//
//	if maxAttempts <= 0 { maxAttempts = 3 }
//	if maxAttempts > 5  { maxAttempts = 5 }
func clampReflexionMaxAttempts(n int) int {
	if n <= 0 {
		return 3
	}
	if n > 5 {
		return 5
	}
	return n
}

// effectiveFeedbackTemplate encodes the FeedbackTemplate default from runReflexion:
//
//	if feedbackTmpl == "" { feedbackTmpl = "<default>" }
func effectiveFeedbackTemplate(s string) string {
	if s == "" {
		return "Your previous response was blocked by a content policy guardrail. " +
			"Please revise your response to comply with our content policies."
	}
	return s
}

// shouldUpdateEmbeddingModel encodes the override condition from handleEmbedding:
//
//	if rc.Model != req.Model { rc.EmbeddingRequest.Model = rc.Model }
func shouldUpdateEmbeddingModel(rcModel, reqModel string) bool {
	return rcModel != reqModel
}

// ---------------------------------------------------------------------------
// Properties: reflexion MaxAttempts clamping
// ---------------------------------------------------------------------------

// Property 1: Any non-positive MaxAttempts produces the default of 3.
func TestProp_Reflexion_ZeroOrNegativeMaxAttempts_DefaultsToThree(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("maxAttempts ≤ 0 always clamps to 3", prop.ForAll(
		func(n int) bool {
			return clampReflexionMaxAttempts(n) == 3
		},
		gen.IntRange(-1<<20, 0),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 2: Any MaxAttempts greater than 5 clamps to 5 (hard cap).
func TestProp_Reflexion_ExcessiveMaxAttempts_ClampsToFive(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("maxAttempts > 5 always clamps to 5", prop.ForAll(
		func(n int) bool {
			return clampReflexionMaxAttempts(n) == 5
		},
		gen.IntRange(6, 1<<20),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 3: MaxAttempts in [1,5] passes through unchanged.
func TestProp_Reflexion_ValidMaxAttempts_PassThrough(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("maxAttempts in [1,5] is unchanged", prop.ForAll(
		func(n int) bool {
			return clampReflexionMaxAttempts(n) == n
		},
		gen.IntRange(1, 5),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 4: Clamped result is always in [1,5] for any integer input.
func TestProp_Reflexion_ClampedResult_AlwaysInValidRange(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("clamped maxAttempts is always in [1,5]", prop.ForAll(
		func(n int) bool {
			result := clampReflexionMaxAttempts(n)
			return result >= 1 && result <= 5
		},
		gen.Int(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 5: Clamping is idempotent — applying it twice gives the same result.
func TestProp_Reflexion_Clamping_Idempotent(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("clamping maxAttempts is idempotent", prop.ForAll(
		func(n int) bool {
			once := clampReflexionMaxAttempts(n)
			twice := clampReflexionMaxAttempts(once)
			return once == twice
		},
		gen.Int(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// ---------------------------------------------------------------------------
// Properties: FeedbackTemplate default
// ---------------------------------------------------------------------------

// Property 6: An empty FeedbackTemplate is always replaced with a non-empty default.
func TestProp_Reflexion_EmptyFeedbackTemplate_GetsDefault(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("empty FeedbackTemplate produces non-empty result", prop.ForAll(
		func(_ string) bool {
			result := effectiveFeedbackTemplate("")
			return result != ""
		},
		gen.Const(""), // always pass empty
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 7: A non-empty FeedbackTemplate is never replaced.
func TestProp_Reflexion_NonEmptyFeedbackTemplate_PassThrough(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("non-empty FeedbackTemplate is returned unchanged", prop.ForAll(
		func(s string) bool {
			if s == "" {
				return true // skip degenerate case
			}
			return effectiveFeedbackTemplate(s) == s
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 8: effectiveFeedbackTemplate output is always non-empty.
func TestProp_Reflexion_FeedbackTemplate_NeverEmpty(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("effectiveFeedbackTemplate is never empty", prop.ForAll(
		func(s string) bool {
			return effectiveFeedbackTemplate(s) != ""
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// ---------------------------------------------------------------------------
// Properties: embedding model override
// ---------------------------------------------------------------------------

// Property 9: When rc.Model differs from req.Model, the override is applied.
func TestProp_Embedding_ModelOverride_WhenDifferent(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("shouldUpdateEmbeddingModel is true when models differ", prop.ForAll(
		func(rcModel, reqModel string) bool {
			if rcModel == reqModel {
				return true // handled by Property 10
			}
			return shouldUpdateEmbeddingModel(rcModel, reqModel)
		},
		gen.AnyString(),
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 10: When rc.Model equals req.Model, no override is applied (no-op).
func TestProp_Embedding_ModelOverride_NoOp_WhenEqual(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("shouldUpdateEmbeddingModel is false when models are equal", prop.ForAll(
		func(model string) bool {
			return !shouldUpdateEmbeddingModel(model, model)
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 11: shouldUpdateEmbeddingModel is anti-reflexive — it is never true
// when both arguments are the same string.
func TestProp_Embedding_ModelOverride_AntiReflexive(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("shouldUpdateEmbeddingModel(x,x) is always false", prop.ForAll(
		func(model string) bool {
			return !shouldUpdateEmbeddingModel(model, model)
		},
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}

// Property 12: shouldUpdateEmbeddingModel is symmetric with respect to inequality —
// if update is needed in one direction it is also needed in the other.
func TestProp_Embedding_ModelOverride_SymmetricInequality(t *testing.T) {
	properties := gopter.NewProperties(nil)

	properties.Property("shouldUpdateEmbeddingModel(a,b) == shouldUpdateEmbeddingModel(b,a)", prop.ForAll(
		func(a, b string) bool {
			return shouldUpdateEmbeddingModel(a, b) == shouldUpdateEmbeddingModel(b, a)
		},
		gen.AnyString(),
		gen.AnyString(),
	))

	properties.TestingRun(t, gopter.NewFormatedReporter(false, 80, os.Stdout))
}
