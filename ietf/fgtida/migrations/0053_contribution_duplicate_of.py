from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0052_session_material_resource_types')]
    operations = [migrations.AddField(model_name='contribution', name='duplicate_of',
        field=models.ForeignKey(blank=True, null=True, editable=False, on_delete=django.db.models.deletion.PROTECT,
            related_name='preserved_duplicates', to='fgtida.contribution'))]
