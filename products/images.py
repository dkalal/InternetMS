from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PRODUCT_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PRODUCT_IMAGE_PIXELS = 24_000_000
PRODUCT_IMAGE_BOUNDING_BOX = (1600, 1600)


def product_image_upload_to(instance, filename):
    """Store product photos in a tenant-specific, non-guessable location."""
    tenant_key = instance.tenant_id or instance.organization_id or 'unassigned'
    extension = Path(filename).suffix.lower() or '.webp'
    return f'product_images/tenant_{tenant_key}/{uuid4().hex}{extension}'


def validate_product_image_size(image):
    """Reject oversized new uploads without reading an existing storage object.

    ``Model.full_clean()`` also runs field validators for already-committed
    ``FieldFile`` values. Reopening those objects makes an ordinary model save
    depend on local/object storage availability and breaks valid legacy file
    references. New assignments are marked uncommitted by Django's file
    descriptor, so upload validation still happens before the file is saved.
    """
    if not image or getattr(image, '_committed', False):
        return
    if image.size > MAX_PRODUCT_IMAGE_BYTES:
        raise ValidationError('Product image must be 6 MB or smaller.')


def prepare_product_image(uploaded_image):
    """Normalize a validated upload into a lightweight, metadata-free WebP file.

    Product photos are displayed repeatedly in catalog grids, so keeping the
    original phone-camera payload would make the selling screen progressively
    slower. The conversion also strips embedded metadata and normalizes EXIF
    orientation while preserving transparency where present.
    """
    validate_product_image_size(uploaded_image)
    try:
        uploaded_image.seek(0)
        with Image.open(uploaded_image) as source:
            width, height = source.size
            if width * height > MAX_PRODUCT_IMAGE_PIXELS:
                raise ValidationError('Product image dimensions are too large. Use an image below 24 megapixels.')
            normalized = ImageOps.exif_transpose(source)
            normalized.load()
            has_alpha = normalized.mode in {'RGBA', 'LA'} or (
                normalized.mode == 'P' and 'transparency' in normalized.info
            )
            normalized = normalized.convert('RGBA' if has_alpha else 'RGB')
            normalized.thumbnail(PRODUCT_IMAGE_BOUNDING_BOX, Image.Resampling.LANCZOS)

            output = BytesIO()
            normalized.save(output, format='WEBP', quality=84, method=4)
    except ValidationError:
        raise
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError('Upload a valid JPEG, PNG, or WebP product image.') from exc
    finally:
        uploaded_image.seek(0)

    return ContentFile(output.getvalue(), name=f'{uuid4().hex}.webp')
