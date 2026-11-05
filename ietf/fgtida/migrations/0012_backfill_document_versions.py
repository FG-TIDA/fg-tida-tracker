from django.db import migrations
from django.db.models import Min


def backfill(apps,schema_editor):
    Group=apps.get_model('group','Group')
    selected=set(Group.objects.filter(acronym='tida').values_list('pk',flat=True))
    frontier=selected.copy()
    while frontier:
        frontier=set(Group.objects.filter(parent_id__in=frontier).values_list('pk',flat=True))-selected
        selected.update(frontier)
    Event=apps.get_model('doc','NewRevisionDocEvent')
    Version=apps.get_model('fgtida','DocumentVersion')
    Release=apps.get_model('fgtida','WorkItemRelease')
    releases={(row.work_item.document_id,row.revision):row.tag.removeprefix('v') for row in Release.objects.select_related('work_item') if row.work_item.document_id}
    rows=Event.objects.filter(doc__group_id__in=selected).exclude(rev='').values('doc_id','rev').annotate(recorded=Min('time')).order_by('doc_id','recorded','rev')
    previous={}
    for row in rows.iterator():
        last=previous.get(row['doc_id'])
        label=releases.get((row['doc_id'],row['rev'])) or (f'{last[0]}.{last[1]+1}' if last else '0.0')
        import re
        if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',label):
            raise RuntimeError('Existing release uses a non-X.Y version. Review its archive mapping before migration; no archive was changed.')
        previous[row['doc_id']]=tuple(map(int,label.split('.')))
        Version.objects.get_or_create(document_id=row['doc_id'],revision=row['rev'],defaults={'version':label,'recorded':row['recorded']})


class Migration(migrations.Migration):
    dependencies=[('fgtida','0011_documentversion')]
    operations=[migrations.RunPython(backfill,migrations.RunPython.noop)]
