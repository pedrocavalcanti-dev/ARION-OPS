from django.db import migrations


def create_management_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.using(schema_editor.connection.alias).get_or_create(name="Gestão")


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("configuracoes", "0010_alter_groupdatascope_options"),
    ]

    operations = [migrations.RunPython(create_management_group, migrations.RunPython.noop)]
