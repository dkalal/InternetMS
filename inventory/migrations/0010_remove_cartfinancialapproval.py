from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("inventory", "0009_cartfinancialapproval")]

    operations = [
        migrations.DeleteModel(name="CartFinancialApproval"),
    ]
