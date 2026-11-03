import django.core.validators
from django.db import migrations, models

import products.images


class Migration(migrations.Migration):
    dependencies = [
        ('products', '0015_remove_product_customer'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='image',
            field=models.ImageField(
                blank=True,
                help_text='Optional catalog photo. JPEG, PNG, or WebP up to 6 MB.',
                null=True,
                upload_to=products.images.product_image_upload_to,
                validators=[
                    django.core.validators.FileExtensionValidator(
                        allowed_extensions=['jpg', 'jpeg', 'png', 'webp']
                    ),
                    products.images.validate_product_image_size,
                ],
            ),
        ),
    ]
