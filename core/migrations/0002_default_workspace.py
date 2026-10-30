from django.db import migrations


def seed_workspace(apps, schema_editor):
    apps.get_model('core', 'Workspace').objects.using(schema_editor.connection.alias).get_or_create(
        id='default', defaults={'name': 'Local Workspace'})


class Migration(migrations.Migration):
    dependencies = [('core', '0001_initial')]
    operations = [migrations.RunPython(seed_workspace, migrations.RunPython.noop)]
