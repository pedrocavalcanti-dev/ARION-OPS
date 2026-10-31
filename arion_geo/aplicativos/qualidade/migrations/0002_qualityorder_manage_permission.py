from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("qualidade", "0001_initial")]

    operations = [
        migrations.AlterModelOptions(
            name="qualityorder",
            options={
                "ordering": ("-created_at", "-pk"),
                "permissions": (
                    ("view_quality", "Can view Quality module"),
                    ("manage_quality_orders", "Can manage Quality Orders"),
                ),
                "verbose_name": "Ordem de Qualidade",
                "verbose_name_plural": "Ordens de Qualidade",
            },
        ),
    ]
