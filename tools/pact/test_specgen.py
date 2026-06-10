"""Tests for pact spec gen (specgen.py)."""

import pytest
from .specgen import synthesize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec(src: str, name: str = "Test") -> str:
    return synthesize(src, name)


# ---------------------------------------------------------------------------
# Model extraction
# ---------------------------------------------------------------------------

class TestModelExtraction:
    def test_detects_django_model_class(self):
        src = """
from django.db import models
class Widget(models.Model):
    name = models.CharField(max_length=100)
    price = models.IntegerField()
"""
        spec = _spec(src)
        assert "VARIABLES" in spec
        assert "widgets" in spec
        assert "SET OF Widget records" in spec

    def test_string_field_maps_to_STRING(self):
        src = """
from django.db import models
class Foo(models.Model):
    title = models.CharField(max_length=50)
"""
        spec = _spec(src)
        assert "r.title \\in STRING" in spec

    def test_integer_field_maps_to_Int(self):
        src = """
from django.db import models
class Foo(models.Model):
    count = models.IntegerField()
"""
        spec = _spec(src)
        assert "r.count \\in Int" in spec

    def test_positive_integer_maps_to_Nat(self):
        src = """
from django.db import models
class Foo(models.Model):
    retries = models.PositiveIntegerField()
"""
        spec = _spec(src)
        assert "r.retries \\in Nat" in spec

    def test_boolean_field_maps_to_BOOLEAN(self):
        src = """
from django.db import models
class Foo(models.Model):
    active = models.BooleanField(default=True)
"""
        spec = _spec(src)
        assert "r.active \\in BOOLEAN" in spec

    def test_nullable_field_includes_NULL(self):
        src = """
from django.db import models
class Foo(models.Model):
    notes = models.TextField(null=True)
"""
        spec = _spec(src)
        assert '"NULL"' in spec

    def test_init_starts_empty(self):
        src = """
from django.db import models
class Foo(models.Model):
    x = models.CharField()
"""
        spec = _spec(src)
        assert "foos = {}" in spec

    def test_no_models_produces_stub(self):
        src = "x = 1\n"
        spec = _spec(src)
        assert "no Django models or Celery tasks found" in spec


# ---------------------------------------------------------------------------
# Unique constraints
# ---------------------------------------------------------------------------

class TestUniqueConstraints:
    def test_unique_together_extracted(self):
        src = """
from django.db import models
class Booking(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE)
    slot = models.IntegerField()
    class Meta:
        unique_together = [['user', 'slot']]
"""
        spec = _spec(src)
        assert "UserSlotUnique" in spec
        assert "r1.user" in spec
        assert "r1.slot" in spec

    def test_unique_constraint_object_extracted(self):
        src = """
from django.db import models
from django.db.models import Q
class Config(models.Model):
    dataset = models.ForeignKey('Dataset', on_delete=models.CASCADE)
    template = models.ForeignKey('Template', on_delete=models.CASCADE)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                condition=Q(deleted=False),
                fields=['dataset', 'template'],
                name='unique_active_config',
            )
        ]
"""
        spec = _spec(src)
        assert "DatasetTemplateUnique" in spec


# ---------------------------------------------------------------------------
# Task extraction
# ---------------------------------------------------------------------------

class TestTaskExtraction:
    def test_shared_task_produces_action(self):
        src = """
from celery import shared_task
@shared_task
def process_batch(batch_id):
    pass
"""
        spec = _spec(src)
        assert "ProcessBatch" in spec
        assert "Corresponds to: @shared_task process_batch()" in spec

    def test_task_with_args_quantified_in_next(self):
        src = """
from celery import shared_task
@shared_task
def run_eval(job_id, model_id):
    pass
"""
        spec = _spec(src)
        assert "\\E _job_id \\in STRING" in spec
        assert "\\E _model_id \\in STRING" in spec
        assert "RunEval(_job_id, _model_id)" in spec

    def test_no_arg_task_in_next_no_quantifier(self):
        src = """
from celery import shared_task
@shared_task
def heartbeat():
    pass
"""
        spec = _spec(src)
        assert "\\/ Heartbeat" in spec
        # No \\E for no-arg task
        assert "\\E" not in spec

    def test_cache_get_and_set_flags_non_atomic(self):
        src = """
from celery import shared_task
from django.core.cache import cache
@shared_task
def update_counter(key):
    val = cache.get(key)
    cache.set(key, val + 1)
"""
        spec = _spec(src)
        assert "non-atomic" in spec
        assert "split step" in spec

    def test_cache_get_only_no_warning(self):
        src = """
from celery import shared_task
from django.core.cache import cache
@shared_task
def read_config(key):
    return cache.get(key)
"""
        spec = _spec(src)
        assert "non-atomic" not in spec


# ---------------------------------------------------------------------------
# Spec structure
# ---------------------------------------------------------------------------

class TestSpecStructure:
    def test_module_header_present(self):
        src = "from django.db import models\nclass X(models.Model):\n    n = models.IntegerField()\n"
        spec = _spec(src, "MyMod")
        assert "MODULE MyMod" in spec

    def test_extends_line_present(self):
        src = "x = 1\nclass A(models.Model): n = models.IntegerField()\n"
        spec = _spec(src)
        assert "EXTENDS Naturals, Sequences, FiniteSets, TLC" in spec

    def test_type_invariant_declared(self):
        src = "from django.db import models\nclass Y(models.Model):\n    v = models.CharField()\n"
        spec = _spec(src)
        assert "INVARIANT TypeInvariant" in spec

    def test_spec_formula_present(self):
        src = "from django.db import models\nclass Z(models.Model):\n    k = models.IntegerField()\n"
        spec = _spec(src)
        assert "Spec ==" in spec
        assert "Init" in spec
        assert "[][Next]_" in spec

    def test_placeholder_variable_when_tasks_only(self):
        src = """
from celery import shared_task
@shared_task
def do_work():
    pass
"""
        spec = _spec(src)
        assert "task_state" in spec
        assert "WF_<<task_state>>(Next)" in spec
