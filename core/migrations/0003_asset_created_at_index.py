from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0002_default_workspace')]
    operations = [migrations.SeparateDatabaseAndState(
        database_operations=[migrations.RunSQL(
            'CREATE INDEX IF NOT EXISTS ix_assets_created_at ON assets (created_at)',
            'DROP INDEX IF EXISTS ix_assets_created_at',
        )],
        state_operations=[migrations.AddIndex(
            model_name='asset', index=models.Index(fields=['created_at'], name='ix_assets_created_at'))],
    )]
