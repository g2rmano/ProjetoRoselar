from django.db import migrations


def migrate_open_to_pending(apps, schema_editor):
    """Orders created when 'OPEN' was a valid status have no Portuguese label.
    Map them to PENDING ('Aguardando Aprovação'), the closest equivalent."""
    Order = apps.get_model('sales', 'Order')
    Order.objects.filter(status='OPEN').update(status='PENDING')


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0018_remove_legacy_statuses_add_transport_info'),
    ]

    operations = [
        migrations.RunPython(migrate_open_to_pending, migrations.RunPython.noop),
    ]
