from django.contrib import admin

from .models import Product, ProductCategory, UnitOfMeasure

# Register your models here.

admin.site.register(Product)
admin.site.register(ProductCategory)
admin.site.register(UnitOfMeasure)
