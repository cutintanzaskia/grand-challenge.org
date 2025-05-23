from django.core.exceptions import ValidationError
from django.db.models.signals import (
    m2m_changed,
    post_save,
    pre_delete,
    pre_save,
)
from django.db.transaction import on_commit
from django.dispatch import receiver

from grandchallenge.cases.models import Image
from grandchallenge.reader_studies.models import DisplaySet


@receiver(m2m_changed, sender=DisplaySet.values.through)
def update_permissions_on_display_set_changed(
    instance, action, reverse, pk_set, **_
):
    if action not in ["post_add", "post_remove", "pre_clear"]:
        # nothing to do for the other actions
        return

    if reverse:
        images = Image.objects.filter(componentinterfacevalue__pk=instance.pk)
    else:
        if pk_set is None:
            # When using a _clear action, pk_set is None
            # https://docs.djangoproject.com/en/2.2/ref/signals/#m2m-changed
            images = [
                civ.image
                for civ in instance.values.filter(image__isnull=False)
            ]
        else:
            images = Image.objects.filter(
                componentinterfacevalue__pk__in=pk_set
            )

    def update_permissions():
        for image in images:
            image.update_viewer_groups_permissions()

    on_commit(update_permissions)


@receiver(pre_delete, sender=DisplaySet)
@receiver(post_save, sender=DisplaySet)
def update_view_image_permissions(*_, instance: DisplaySet, **__):
    images = [civ.image for civ in instance.values.filter(image__isnull=False)]

    def update_permissions():
        for image in images:
            image.update_viewer_groups_permissions()

    on_commit(update_permissions)


@receiver(m2m_changed, sender=DisplaySet.values.through)
def assert_modification_allowed(instance, action, reverse, model, pk_set, **_):
    if "pre" not in action:
        return

    if reverse:
        not_editable = DisplaySet.objects.filter(
            answers__isnull=False
        ).values_list("pk", flat=True)
        if len(not_editable) > 0:
            raise ValidationError(
                "The following display sets cannot be updated, because answers "
                f"for them already exist: {', '.join(map(str, not_editable))}"
            )

    else:
        if not instance.is_editable:
            raise ValidationError(
                "This display set cannot be updated, because answers for it "
                "already exist."
            )


@receiver(pre_save, sender=DisplaySet)
def set_display_set_order(sender, instance, **_):
    """Set to first multiple of 10 that is higher than the highest in this rs."""
    if instance.order:
        return
    instance.order = instance.reader_study.next_display_set_order
