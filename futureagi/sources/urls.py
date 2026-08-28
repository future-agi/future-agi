from rest_framework.routers import DefaultRouter
from sources.views import SourceBookViewSet, ScanPageViewSet, TargetPassageViewSet

router = DefaultRouter()
router.register(r"books", SourceBookViewSet, basename="book")
router.register(r"pages", ScanPageViewSet, basename="page")
router.register(r"passages", TargetPassageViewSet, basename="passage")

urlpatterns = router.urls
