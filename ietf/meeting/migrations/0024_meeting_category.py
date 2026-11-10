import django.db.models.deletion
from django.db import migrations, models


def seed_categories(apps, schema_editor):
    category = apps.get_model('meeting', 'MeetingCategory')
    for name in ('Plenary', 'Working group meeting', 'Preparation meeting', 'Workshop'):
        category.objects.using(schema_editor.connection.alias).get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [('meeting', '0023_floorplan_file')]

    operations = [
        migrations.CreateModel(
            name='MeetingCategory',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
            ],
            options={'ordering': ['name'], 'verbose_name_plural': 'meeting categories'},
        ),
        migrations.AddField(
            model_name='meeting', name='category',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                to='meeting.meetingcategory', help_text='Optional public category, such as Plenary, Preparation meeting or Workshop.'),
        ),
        migrations.RunPython(seed_categories, migrations.RunPython.noop),
    ]
