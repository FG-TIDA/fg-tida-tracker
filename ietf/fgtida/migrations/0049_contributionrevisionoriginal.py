import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0048_documentformat_is_public')]
    operations = [migrations.CreateModel(
        name='ContributionRevisionOriginal',
        fields=[
            ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('revision', models.CharField(max_length=16)),
            ('filename', models.CharField(max_length=255)),
            ('content', models.BinaryField(editable=False)),
            ('sha256', models.CharField(max_length=64)),
            ('created', models.DateTimeField(auto_now_add=True)),
            ('contribution', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='revision_originals', to='fgtida.contribution')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('contribution', 'revision'), name='fgtida_revision_original_unique')]},
    )]
