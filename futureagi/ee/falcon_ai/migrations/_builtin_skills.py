"""Historical skill data steps bound to the migration's database connection."""

from ee.falcon_ai.builtin_skills_loader import load_builtin_skills


def seed_builtin_skills(skill_model, alias):
    created = updated = 0
    skills = skill_model.objects.using(alias)
    for data in load_builtin_skills():
        defaults = {
            "name": data["name"],
            "description": data["description"],
            "icon": data["icon"],
            "instructions": data["instructions"],
            "tool_names": data["tool_names"],
            "example_trajectories": data["example_trajectories"],
            "trigger_phrases": data["trigger_phrases"],
            "is_builtin": True,
            "is_active": True,
            "workspace": None,
        }
        existing = skills.filter(
            slug=data["slug"], is_builtin=True, organization__isnull=True
        ).first()
        if existing:
            dirty = False
            for field, value in defaults.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    dirty = True
            if dirty:
                existing.save(using=alias)
                updated += 1
        else:
            skills.create(organization=None, slug=data["slug"], **defaults)
            created += 1
    return created, updated


def consolidate_per_org_builtins(skill_model, conversation_model, alias):
    deleted = 0
    skills = skill_model.objects.using(alias)
    slugs = skills.filter(is_builtin=True).values_list("slug", flat=True).distinct()
    for slug in slugs:
        rows = list(skills.filter(slug=slug, is_builtin=True).order_by("created_at"))
        if not rows:
            continue
        canonical = rows[0]
        if canonical.organization_id is not None:
            canonical.organization = None
            canonical.save(using=alias, update_fields=["organization"])
        other_ids = [row.id for row in rows[1:]]
        if other_ids:
            conversation_model.objects.using(alias).filter(
                active_skill_id__in=other_ids
            ).update(active_skill=canonical)
            skills.filter(id__in=other_ids).delete()
            deleted += len(other_ids)
    return deleted
