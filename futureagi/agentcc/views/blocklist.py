import structlog
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.blocklist import AgentccBlocklist
from agentcc.serializers.blocklist import AgentccBlocklistSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccBlocklistViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """CRUD for named word blocklists. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccBlocklistSerializer
    queryset = AgentccBlocklist.no_workspace_objects.all()
    _gm = GeneralMethods()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccBlocklistSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("blocklist_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(AgentccBlocklistSerializer(instance).data)
        except Exception as e:
            logger.exception("blocklist_retrieve_error", error=str(e))
            return self._gm.not_found("Blocklist not found")

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccBlocklistSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            blocklist = serializer.save(organization=org)
            return self._gm.success_response(AgentccBlocklistSerializer(blocklist).data)
        except Exception as e:
            logger.exception("blocklist_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccBlocklistSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            blocklist = serializer.save()
            return self._gm.success_response(AgentccBlocklistSerializer(blocklist).data)
        except Exception as e:
            logger.exception("blocklist_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("blocklist_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="add-words")
    def add_words(self, request, pk=None):
        """Add words to the blocklist (deduplicates)."""
        try:
            instance = self.get_object()
            new_words = request.data.get("words", [])
            if not isinstance(new_words, list):
                return self._gm.bad_request("words must be a list")
            for index, word in enumerate(new_words):
                if not isinstance(word, str):
                    return self._gm.bad_request(
                        f"Word at index {index} must be a string"
                    )

            existing = set(instance.words)
            for word in new_words:
                existing.add(word)
            instance.words = sorted(existing)
            instance.save(update_fields=["words", "updated_at"])

            return self._gm.success_response(AgentccBlocklistSerializer(instance).data)
        except Exception as e:
            logger.exception("blocklist_add_words_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"], url_path="remove-words")
    def remove_words(self, request, pk=None):
        """Remove words from the blocklist."""
        try:
            instance = self.get_object()
            remove = request.data.get("words", [])
            if not isinstance(remove, list):
                return self._gm.bad_request("words must be a list")
            for index, word in enumerate(remove):
                if not isinstance(word, str):
                    return self._gm.bad_request(
                        f"Word at index {index} must be a string"
                    )

            remove_set = set(remove)
            instance.words = [w for w in instance.words if w not in remove_set]
            instance.save(update_fields=["words", "updated_at"])

            return self._gm.success_response(AgentccBlocklistSerializer(instance).data)
        except Exception as e:
            logger.exception("blocklist_remove_words_error", error=str(e))
            return self._gm.bad_request(str(e))
