from django.db import migrations, models


def normalize_roles_forward(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="OWNER").update(role="ADMIN")
    User.objects.filter(role="STAFF").update(role="FINANCE")


def normalize_roles_reverse(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="FINANCE").update(role="STAFF")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_alter_user_role"),
    ]

    operations = [
        migrations.RunPython(normalize_roles_forward, normalize_roles_reverse),
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("SELLER", "Vendedor"),
                    ("FINANCE", "Financeiro"),
                    ("ADMIN", "Admin"),
                ],
                default="SELLER",
                max_length=10,
                verbose_name="Perfil",
            ),
        ),
    ]
