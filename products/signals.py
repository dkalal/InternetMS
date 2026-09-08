from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Product


def _delete_file_after_commit(storage, name):
    if name:
        transaction.on_commit(lambda: storage.delete(name))


@receiver(pre_save, sender=Product)
def remember_replaced_product_image(sender, instance, **kwargs):
    """Remember an old image without weakening tenant-scoped application reads."""
    instance._replaced_image_name = ''
    if not instance.pk:
        return
    previous_name = sender.objects.unscoped().filter(pk=instance.pk).values_list('image', flat=True).first() or ''
    current_name = instance.image.name if instance.image else ''
    if previous_name and previous_name != current_name:
        instance._replaced_image_name = previous_name


@receiver(post_save, sender=Product)
def delete_replaced_product_image(sender, instance, **kwargs):
    previous_name = getattr(instance, '_replaced_image_name', '')
    if previous_name:
        _delete_file_after_commit(instance.image.storage, previous_name)


@receiver(post_delete, sender=Product)
def delete_removed_product_image(sender, instance, **kwargs):
    if instance.image:
        _delete_file_after_commit(instance.image.storage, instance.image.name)
