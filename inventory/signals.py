from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from .models import Cart, CartLine, CartSerialSelection


@receiver(post_save, sender=CartLine)
@receiver(post_delete, sender=CartLine)
def cart_line_activity(sender, instance, **kwargs):
    Cart.objects.unscoped().filter(pk=instance.cart_id, status=Cart.Status.DRAFT).update(updated_at=timezone.now())


@receiver(post_save, sender=CartSerialSelection)
@receiver(post_delete, sender=CartSerialSelection)
def cart_serial_activity(sender, instance, **kwargs):
    cart_id = CartLine.objects.unscoped().filter(pk=instance.cart_line_id).values_list('cart_id', flat=True).first()
    Cart.objects.unscoped().filter(pk=cart_id, status=Cart.Status.DRAFT).update(updated_at=timezone.now())
