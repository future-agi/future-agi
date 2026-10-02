import logging
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
logging.disable(logging.CRITICAL)
import django

django.setup()

from ee.falcon_ai.models import Conversation, Message

# Check all recent conversations
recent = Conversation.objects.order_by("-created_at")[:10]
print(f"Recent conversations: {recent.count()}")
for c in recent:
    msgs = Message.objects.filter(conversation=c).count()
    hidden = c.metadata.get("hidden", False) if c.metadata else False
    print(
        f"  {'[HIDDEN]' if hidden else '[USER]  '} {c.title[:40]:40s}  msgs={msgs}  {c.created_at.strftime('%H:%M:%S')}"
    )
