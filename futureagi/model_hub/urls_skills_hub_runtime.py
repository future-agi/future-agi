"""Load projection shared by both resource kinds."""

from django.urls import path

from model_hub.views.skills_hub_runtime import SkillFilterValuesView, SkillLoadsView

urlpatterns = [
    path("loads/", SkillLoadsView.as_view(), name="skills-loads"),
    path("filter-values/", SkillFilterValuesView.as_view(), name="skills-filter-values"),
]
