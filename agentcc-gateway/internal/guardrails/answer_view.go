package guardrails

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// answerView is the answer the checks on the answer read. The checks read a
// choice's content; a model's refusal and an audio answer's transcript are
// text the client gets too, so they are read as content: when a choice
// carries one, the view is a copy of the answer whose choices also hold each
// such text as the content of a choice of its own, after the answer's own
// choices. Otherwise it is the answer itself. Audio with no transcript is
// not text the checks can read (unreadableAnswer).
type answerView struct {
	orig  *models.ChatCompletionResponse
	resp  *models.ChatCompletionResponse
	own   int          // how many of resp's choices are the answer's own
	texts []answerText // per added choice: where its text came from
}

// answerText is where an added choice's text is in the answer.
type answerText struct {
	choice     int             // the answer's choice that carries it
	transcript bool            // an audio transcript, else a refusal
	read       json.RawMessage // the content as built, to tell a rewrite
}

func newAnswerView(resp *models.ChatCompletionResponse) *answerView {
	v := &answerView{orig: resp, resp: resp}
	if resp == nil {
		return v
	}
	var added []models.Choice
	add := func(i int, text string, transcript bool) {
		content, _ := json.Marshal(text) // a string always encodes
		added = append(added, models.Choice{Index: resp.Choices[i].Index,
			Message: models.Message{Role: "assistant", Content: content}})
		v.texts = append(v.texts, answerText{choice: i, transcript: transcript, read: content})
	}
	for i, c := range resp.Choices {
		if r := c.Message.Refusal; r != nil && *r != "" {
			add(i, *r, false)
		}
		if a := c.Message.Audio; a != nil && a.Transcript != "" {
			add(i, a.Transcript, true)
		}
	}
	if len(added) == 0 {
		return v
	}
	view := *resp
	view.Choices = append(append(make([]models.Choice, 0, len(resp.Choices)+len(added)), resp.Choices...), added...)
	v.resp, v.own = &view, len(resp.Choices)
	return v
}

// errTranscriptRewrite is a check's rewrite of an audio answer's transcript
// (a remediation), which cannot be applied: the audio would still say it.
var errTranscriptRewrite = errors.New("a check rewrote the transcript of an audio answer, and the gateway cannot rewrite the audio itself")

// writeBack applies to the answer what a check rewrote in the view (a
// remediation, pii-detection's mask): the answer's own choices as the checks
// left them, and a rewritten refusal into the refusal. A rewritten
// transcript is an error (errTranscriptRewrite): the answer is withheld
// rather than served with audio that still says what the check rewrote.
func (v *answerView) writeBack() error {
	if v == nil || v.resp == v.orig {
		return nil
	}
	for k, t := range v.texts {
		got := v.resp.Choices[v.own+k].Message.Content
		if bytes.Equal(got, t.read) {
			continue
		}
		if t.transcript {
			return errTranscriptRewrite
		}
		text, ok := ContentText(got)
		if !ok {
			return errors.New("a check rewrote a refusal into content that is not text")
		}
		refusal := text
		v.resp.Choices[t.choice].Message.Refusal = &refusal
	}
	copy(v.orig.Choices, v.resp.Choices[:v.own])
	return nil
}

// ShapeCheck is implemented by a check that judges the answer's shape (a
// format, a pattern the whole answer must match, a length, the answer's
// JSON) rather than what its text says. A model's refusal is not an answer
// in that shape (structured outputs hand it to the application on
// purpose), so such a check does not read the refusal; it reads the
// content a choice carries (answerInputFor). Every other check reads a
// refusal as content.
type ShapeCheck interface {
	JudgesShape() bool
}

// IsShapeCheck says whether g judges the answer's shape (ShapeCheck).
func IsShapeCheck(g Guardrail) bool {
	s, ok := g.(ShapeCheck)
	return ok && s.JudgesShape()
}

// UnknownKindCheck is implemented by a check whose kind the gateway cannot
// tell: what it judges is set outside the gateway (a Future AGI evaluation
// id, a webhook, an external evaluator). Such a check does not read a
// model's refusal (answerInputFor): it reads the answer as production gave
// it to the checks, which kept no refusal, so a refusal is served as
// production served it.
type UnknownKindCheck interface {
	KindUnknown() bool
}

// IsUnknownKindCheck says whether the gateway cannot tell g's kind
// (UnknownKindCheck).
func IsUnknownKindCheck(g Guardrail) bool {
	u, ok := g.(UnknownKindCheck)
	return ok && u.KindUnknown()
}

// answerInputFor is input as check g reads the answer. A shape check and a
// check of unknown kind read the answer without its refusals
// (withoutRefusals): each choice's content and every other choice as
// production gave them to the checks, which kept no refusal; the rest of
// what they read (the request) is unchanged. A shape check also gets the
// refusals' text apart (CheckInput.Refusals), for its rules that judge what
// the text says. Every other check reads input as it is. When a refusal is withheld from a check, a log line says so,
// with ids and the check's kind only.
func answerInputFor(ctx context.Context, g Guardrail, input *CheckInput) *CheckInput {
	if input == nil || input.Response == nil {
		return input
	}
	shape := IsShapeCheck(g)
	if !shape && !IsUnknownKindCheck(g) {
		return input
	}
	v, ok := ctx.Value(answerViewKey{}).(*answerView)
	if !ok || v == nil || input.Response != v.resp {
		return input
	}
	answer, withheld := v.withoutRefusals(shape)
	if withheld == 0 {
		return input
	}
	kind := "unknown"
	if shape {
		kind = "shape"
	}
	slog.InfoContext(ctx, "model refusal withheld from an output check",
		"request_id", models.GetRequestID(ctx),
		"org_id", input.Metadata["org_id"],
		"check", g.Name(),
		"check_kind", kind,
		"refusals", withheld,
	)
	cp := *input
	cp.Response = answer
	if shape {
		cp.Refusals = v.refusals()
	}
	return &cp
}

// refusals are the texts of the answer's refusals, in choice order.
func (v *answerView) refusals() []string {
	var out []string
	for _, c := range v.resp.Choices[:v.ownCount()] {
		if r := c.Message.Refusal; r != nil && *r != "" {
			out = append(out, *r)
		}
	}
	return out
}

// withoutRefusals is the answer without its refusals, as production (which
// kept no refusal) gave it to the checks, and how many refusals it leaves
// out: the answer's own choices, each with its refusal left out of a copy
// (the answer keeps it) and its content kept, and the transcripts the view
// adds. When dropEmpty is set (a shape check), a choice that carries a
// refusal and no content is left out too, with its transcript. The view
// itself, and 0, when no choice carries a refusal.
func (v *answerView) withoutRefusals(dropEmpty bool) (*models.ChatCompletionResponse, int) {
	refused := func(c models.Choice) bool { return c.Message.Refusal != nil && *c.Message.Refusal != "" }
	withheld := 0
	for _, c := range v.resp.Choices[:v.ownCount()] {
		if refused(c) {
			withheld++
		}
	}
	if withheld == 0 {
		return v.resp, 0
	}
	out := *v.resp
	out.Choices = nil
	kept := make(map[int]bool, v.ownCount())
	for i, c := range v.resp.Choices[:v.ownCount()] {
		if refused(c) {
			if dropEmpty && !hasContent(c.Message.Content) {
				continue
			}
			c.Message.Refusal = nil // c is a copy: the answer keeps its refusal
		}
		kept[i] = true
		out.Choices = append(out.Choices, c)
	}
	for k, t := range v.texts {
		if t.transcript && kept[t.choice] {
			out.Choices = append(out.Choices, v.resp.Choices[v.own+k])
		}
	}
	return &out, withheld
}

// hasContent says whether a message's content is set: neither absent nor
// JSON null.
func hasContent(content json.RawMessage) bool {
	t := bytes.TrimSpace(content)
	return len(t) > 0 && !bytes.Equal(t, []byte("null"))
}

// ownCount is how many of the view's choices are the answer's own.
func (v *answerView) ownCount() int {
	if v.resp == v.orig {
		return len(v.resp.Choices)
	}
	return v.own
}

// unreadableAnswer says whether a choice of resp carries audio with no
// transcript, which no check on the answer can read.
func unreadableAnswer(resp *models.ChatCompletionResponse) bool {
	if resp == nil {
		return false
	}
	for _, c := range resp.Choices {
		if a := c.Message.Audio; a != nil && a.Transcript == "" {
			return true
		}
	}
	return false
}

type answerViewKey struct{}

// withAnswerView puts the answer the checks on the answer read on ctx, for
// the organization's checks (runOrgGuardrails).
func withAnswerView(ctx context.Context, v *answerView) context.Context {
	return context.WithValue(ctx, answerViewKey{}, v)
}

// answerForChecks is the answer the checks on the answer read: ctx's view,
// or rc.Response.
func answerForChecks(ctx context.Context, rc *models.RequestContext) *models.ChatCompletionResponse {
	if v, ok := ctx.Value(answerViewKey{}).(*answerView); ok && v != nil && v.orig == rc.Response {
		return v.resp
	}
	return rc.Response
}
