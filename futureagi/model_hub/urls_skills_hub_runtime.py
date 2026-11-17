"""Load projection shared by both resource kinds."""

from django.urls import path

from model_hub.views.skills_hub_runtime import SkillLoadsView

urlpatterns = [path("loads/", SkillLoadsView.as_view(), name="skills-loads")]
