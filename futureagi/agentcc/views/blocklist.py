import structlog
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_501_NOT_IMPLEMENTED
from rest_framework.viewsets import ModelViewSet

from agentcc.models.blocklist import AgentccBlocklist
from agentcc.serializers.blocklist import AgentccBlocklistSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# docs-agentcc-711, item 1: a stored blocklist never reaches the gateway. The
# org config push does not read blocklists, and the gateway's keyword-blocklist
# check blocks only the words in its own config.words, so a list saved here
# blocked nothing. Every write answers 501; stored lists stay readable.
BLOCKLISTS_NOT_SUPPORTED = "blocklists_not_supported"
BLOCKLISTS_NOT_SUPPORTED_MESSAGE = (
    "Stored blocklists are not supported: the gateway never reads them. Put "
    "the words in the keyword-blocklist check's config.words instead."
)


class AgentccBlocklistViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """Stored word blocklists, read only. Org-scoped.

    Not supported: a stored list never reaches the gateway, whose
    keyword-blocklist check blocks only the words in its config.words. Every
    write (create, update, delete, add-words, remove-words) answers 501
    ``blocklists_not_supported``.
    """

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
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    def update(self, request, *args, **kwargs):
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    def partial_update(self, request, *args, **kwargs):
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    def destroy(self, request, *args, **kwargs):
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    @action(detail=True, methods=["post"], url_path="add-words")
    def add_words(self, request, pk=None):
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    @action(detail=True, methods=["post"], url_path="remove-words")
    def remove_words(self, request, pk=None):
        """Not supported: answers 501 ``blocklists_not_supported``."""
        return self._not_supported()

    def _not_supported(self):
        return self._gm.custom_error_response(
            HTTP_501_NOT_IMPLEMENTED,
            BLOCKLISTS_NOT_SUPPORTED_MESSAGE,
            code=BLOCKLISTS_NOT_SUPPORTED,
        )
