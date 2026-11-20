from pathlib import Path
from django.db import migrations
from django.db.models import Q


def requeue(apps, schema_editor):
    alias = schema_editor.connection.alias
    scans = apps.get_model('fgtida', 'FileScan').objects.using(alias)
    events = apps.get_model('fgtida', 'FileScanEvent').objects.using(alias)
    kinds = {'.pdf', '.doc', '.docx', '.pptx', '.xml', '.txt', '.md', '.rst', '.zip'}
    for record in scans.filter(Q(status='blocked') | (Q(status='clean') & ~Q(rejected_formats=[]))).iterator():
        if Path(record.filename).suffix.lower() not in kinds and not set(record.rejected_formats).intersection({kind[1:] for kind in kinds}):
            continue
        scans.filter(pk=record.pk).update(status='pending', formats=[], rejected_formats=[],
            detail='Queued for a fresh antivirus scan under the document publication policy.')
        events.create(scan_id=record.pk, operation='review', status='pending',
            detail='Legacy document hold requeued. Publication requires fresh antivirus and archive-limit checks.')


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0040_alter_filescan_status')]
    operations = [migrations.RunPython(requeue, migrations.RunPython.noop)]
