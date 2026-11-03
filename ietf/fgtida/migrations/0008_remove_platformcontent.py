from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0007_platformcontent')]
    operations = [migrations.DeleteModel(name='PlatformContent')]
