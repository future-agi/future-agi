"""Regression coverage for the bounded Falcon conversation context window."""

from datetime import timedelta

import pytest
from django.utils import timezone

from ee.falcon_ai.consumers import FalconAIConsumer
from ee.falcon_ai.models import Message


@pytest.mark.django_db
@pytest.mark.parametrize("message_count", [0, 1, 30, 31, 34])
def test_history_returns_latest_30_messages_chronologically(
    conversation, message_count
):
    expected_messages = [
        {
            "role": "user" if number % 2 else "assistant",
            "content": f"message-{number}",
            "tool_calls": (
                [] if number % 2 else [{"name": "lookup", "result": f"result-{number}"}]
            ),
        }
        for number in range(1, message_count + 1)
    ]
    start = timezone.now() - timedelta(minutes=message_count)
    # Insert out of chronological order so only created_at determines the window.
    for number in range(message_count, 0, -1):
        message = Message.objects.create(
            conversation=conversation, **expected_messages[number - 1]
        )
        Message.objects.filter(pk=message.pk).update(
            created_at=start + timedelta(minutes=number)
        )

    consumer = FalconAIConsumer()
    consumer.user = conversation.user
    consumer.organization = conversation.organization
    consumer.workspace = conversation.workspace
    # Follow the neighboring helper tests: call the sync body inside this test's
    # database transaction rather than moving the ORM work to another thread.
    loaded_conversation, history = FalconAIConsumer.__dict__[
        "_get_conversation_and_history"
    ].func(consumer, str(conversation.pk))

    assert loaded_conversation == conversation
    assert history == expected_messages[-30:]
    assert conversation.messages.count() == message_count
