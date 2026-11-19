from django.db import migrations


def grant_manage_quality_contacts(apps, schema_editor):
    using = schema_editor.connection.alias
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    QualityContact = apps.get_model("qualidade", "QualityContact")
    content_type, _ = ContentType.objects.using(using).get_or_create(
        app_label="qualidade",
        model="qualitycontact",
    )
    permission = Permission.objects.using(using).filter(
        content_type=content_type,
        codename="manage_quality_contacts",
    ).first()
    if permission is None:
        permission, _ = Permission.objects.using(using).get_or_create(
            content_type=content_type,
            codename="manage_quality_contacts",
            defaults={"name": "Can manage Quality Contacts"},
        )
    for group_name in ("Administrador do Sistema", "Gestor"):
        group = Group.objects.using(using).filter(name=group_name).first()
        if group is not None:
            group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [
        ("qualidade", "0017_qualitycontact_operational_fields"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(grant_manage_quality_contacts, migrations.RunPython.noop),
    ]
