package otel

import (
	"encoding/json"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
	otelpkg "github.com/futureagi/agentcc-gateway/internal/otel"
	"github.com/futureagi/agentcc-gateway/internal/payloads"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
)

// maxInlineImageBytes bounds an inline image carried on a span. Under it the
// data URI travels and the platform renders the picture; over it the message
// is masked to a description instead. Whole photos are megabytes and would
// dominate a batch for one thumbnail's worth of value.
const maxInlineImageBytes = 64 << 10

// attachMessages emits the flattened message attributes the platform reads to
// render a conversation:
//
//	llm.input_messages.0.message.role
//	llm.input_messages.0.message.content
//	llm.input_messages.0.message.contents.0.message_content.type
//	llm.input_messages.0.message.contents.0.message_content.image.url
//
// input.value carries the same request as one JSON document, which is what an
// evaluator consumes; this is what the trace viewer draws. Without it a vision
// request shows as text with the image silently missing.
// The redactor is threaded through because these attributes carry the same
// user content input.value does. Emitting them unredacted would be a way
// around the org's privacy config that the JSON body already respects.
func (p *Plugin) attachMessages(span *otelpkg.Span, rc *models.RequestContext, r *privacy.Redactor, mode string) {
	scrub := func(s string) string { return redact(payloads.ElideInlineData(s), r, mode) }

	if rc.Request != nil {
		for i, msg := range rc.Request.Messages {
			p.attachMessage(span, "llm.input_messages."+strconv.Itoa(i), msg, scrub)
		}
	}
	if rc.Response != nil {
		for i, choice := range rc.Response.Choices {
			p.attachMessage(span, "llm.output_messages."+strconv.Itoa(i), choice.Message, scrub)
		}
	}
}

func (p *Plugin) attachMessage(span *otelpkg.Span, prefix string, msg models.Message, scrub func(string) string) {
	if msg.Role != "" {
		span.SetAttribute(prefix+".message.role", msg.Role)
	}
	if len(msg.Content) == 0 {
		return
	}

	// Plain string content: the common case, emitted flat.
	var text string
	if err := json.Unmarshal(msg.Content, &text); err == nil {
		span.SetAttribute(prefix+".message.content", scrub(text))
		return
	}

	var parts []messagePart
	if err := json.Unmarshal(msg.Content, &parts); err != nil {
		// Some other shape — hand it over as-is rather than dropping it.
		span.SetAttribute(prefix+".message.content", scrub(string(msg.Content)))
		return
	}

	// A masked message keeps only the flat summary. The viewer prefers a flat
	// string over structured parts for the same message, so emitting both
	// would hide the parts anyway.
	if summary, masked := maskOversizedParts(parts); masked {
		span.SetAttribute(prefix+".message.content", scrub(summary))
		return
	}

	for j, part := range parts {
		base := prefix + ".message.contents." + strconv.Itoa(j) + ".message_content"
		if part.Type != "" {
			span.SetAttribute(base+".type", part.Type)
		}
		if part.Text != "" {
			span.SetAttribute(base+".text", scrub(part.Text))
		}
		// The URL is left intact: under the size bound it is either a link or
		// a small data URI the viewer renders, and scrubbing would break both.
		if part.ImageURL != nil && part.ImageURL.URL != "" {
			span.SetAttribute(base+".image.url", part.ImageURL.URL)
		}
	}
}

type messagePart struct {
	Type     string `json:"type"`
	Text     string `json:"text,omitempty"`
	ImageURL *struct {
		URL    string `json:"url"`
		Detail string `json:"detail,omitempty"`
	} `json:"image_url,omitempty"`
}

// maskOversizedParts reports whether any inline image is too large to carry,
// and if so returns a description of the whole message to send instead.
func maskOversizedParts(parts []messagePart) (string, bool) {
	oversized := false
	for _, part := range parts {
		if part.ImageURL != nil && len(part.ImageURL.URL) > maxInlineImageBytes {
			oversized = true
			break
		}
	}
	if !oversized {
		return "", false
	}

	summary := ""
	for _, part := range parts {
		if summary != "" {
			summary += " "
		}
		switch {
		case part.Text != "":
			summary += part.Text
		case part.ImageURL != nil:
			summary += "[image: " + describeImageURL(part.ImageURL.URL) + "]"
		}
	}
	return summary, true
}

// describeImageURL names an image that is not being carried, so the trace says
// what was sent rather than leaving a hole.
func describeImageURL(url string) string {
	if len(url) <= maxInlineImageBytes {
		return url
	}
	mime := "inline"
	if len(url) > 5 && url[:5] == "data:" {
		for i := 5; i < len(url) && i < 64; i++ {
			if url[i] == ';' {
				mime = url[5:i]
				break
			}
		}
	}
	return mime + ", " + strconv.Itoa(len(url)) + " bytes"
}
