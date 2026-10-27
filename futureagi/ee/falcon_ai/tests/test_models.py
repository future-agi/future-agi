import pytest

from ee.falcon_ai.models import Conversation, FalconUsage, Message


@pytest.mark.django_db
class TestConversation:
    def test_create_conversation(self, user, workspace):
        conv = Conversation.objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            title="Test",
        )
        assert conv.id is not None
        assert conv.title == "Test"
        assert conv.user == user
        assert conv.workspace == workspace

    def test_default_title(self, user, workspace):
        conv = Conversation.objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
        )
        assert conv.title == "New conversation"

    def test_soft_delete(self, conversation):
        conv_id = conversation.id
        conversation.delete()
        # Soft-deleted: still in all_objects but not in default manager
        assert Conversation.all_objects.filter(id=conv_id).exists()
        assert not Conversation.objects.filter(id=conv_id).exists()

    def test_metadata_default(self, conversation):
        assert conversation.metadata == {}

    def test_context_page(self, user, workspace):
        conv = Conversation.objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            context_page="/dashboard/data",
        )
        assert conv.context_page == "/dashboard/data"

    def test_ordering_by_created_at_desc(self, user, workspace):
        """Conversations should be ordered by -created_at."""
        c1 = Conversation.objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            title="First",
        )
        c2 = Conversation.objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            title="Second",
        )
        convs = list(
            Conversation.objects.filter(user=user, organization=user.organization)
        )
        # Most recent first
        assert convs[0].id == c2.id
        assert convs[1].id == c1.id

    def test_str_representation(self, conversation):
        s = str(conversation)
        assert "Conversation(" in s
        assert conversation.title in s


@pytest.mark.django_db
class TestMessage:
    def test_create_message(self, conversation):
        msg = Message.objects.create(
            conversation=conversation, role="user", content="Hello"
        )
        assert msg.id is not None
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_message_ordering(self, conversation):
        m1 = Message.objects.create(
            conversation=conversation, role="user", content="First"
        )
        m2 = Message.objects.create(
            conversation=conversation, role="assistant", content="Second"
        )
        messages = list(Message.objects.filter(conversation=conversation))
        assert messages[0].id == m1.id
        assert messages[1].id == m2.id

    def test_thoughts_default(self, conversation):
        msg = Message.objects.create(
            conversation=conversation, role="assistant", content="Test"
        )
        assert msg.thoughts == []
        assert msg.tool_calls == []
        assert msg.completion_card is None

    def test_feedback(self, message):
        message.feedback = "thumbs_down"
        message.save()
        message.refresh_from_db()
        assert message.feedback == "thumbs_down"

    def test_token_count_and_latency(self, conversation):
        msg = Message.objects.create(
            conversation=conversation,
            role="assistant",
            content="Resp",
            token_count=150,
            latency_ms=1200,
            model_used="gpt-4o-mini",
        )
        msg.refresh_from_db()
        assert msg.token_count == 150
        assert msg.latency_ms == 1200
        assert msg.model_used == "gpt-4o-mini"

    def test_str_representation(self, message):
        s = str(message)
        assert "Message(" in s
        assert "assistant" in s


@pytest.mark.django_db
class TestFalconUsage:
    def test_create_usage(self, conversation, user, workspace):
        usage = FalconUsage.objects.create(
            conversation=conversation,
            organization=user.organization,
            workspace=workspace,
            user=user,
            input_tokens=100,
            output_tokens=200,
            model_used="gpt-4o-mini",
            latency_ms=1500,
        )
        assert usage.input_tokens == 100
        assert usage.output_tokens == 200
        assert usage.model_used == "gpt-4o-mini"
        assert usage.latency_ms == 1500

    def test_usage_defaults(self, conversation, user, workspace):
        usage = FalconUsage.objects.create(
            conversation=conversation,
            organization=user.organization,
            workspace=workspace,
            user=user,
            model_used="gpt-4o-mini",
        )
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.tool_calls_count == 0

    def test_str_representation(self, conversation, user, workspace):
        usage = FalconUsage.objects.create(
            conversation=conversation,
            organization=user.organization,
            workspace=workspace,
            user=user,
            model_used="gpt-4o-mini",
        )
        s = str(usage)
        assert "FalconUsage(" in s
        assert "gpt-4o-mini" in s
