from django.db import migrations, models
import ietf.meeting.models
import ietf.utils.storage


class Migration(migrations.Migration):
    dependencies = [('meeting', '0022_meeting_title')]
    operations = [migrations.AlterField(model_name='floorplan', name='image',
        field=models.FileField(default=None, storage=ietf.utils.storage.BlobShadowFileSystemStorage(kind='floorplan'),
            upload_to=ietf.meeting.models.floorplan_path))]
