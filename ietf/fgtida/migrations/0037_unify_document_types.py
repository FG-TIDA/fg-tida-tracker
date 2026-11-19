from django.db import migrations


def migrate_types(apps, schema_editor):
    alias = schema_editor.connection.alias
    Type = apps.get_model('fgtida', 'PublicDocumentType')
    Attribution = apps.get_model('fgtida', 'DocumentAttribution')
    names = {'themes': 'Theme', 'use-cases': 'Use case', 'templates': 'Template',
             'deliverables': 'Deliverable', 'resources': 'Resource', 'contributions': 'Contribution'}
    for old in Type.objects.using(alias).all():
        target_name = names.get(old.name.lower(), old.name)
        if target_name != old.name:
            target, _ = Type.objects.using(alias).get_or_create(name=target_name)
            Attribution.objects.using(alias).filter(public_type=old).update(public_type=target)
            old.delete(using=alias)
    Doc = apps.get_model('doc', 'Document')
    Group = apps.get_model('group', 'Group')
    group_ids = set(Group.objects.using(alias).filter(acronym='tida').values_list('pk', flat=True))
    frontier = group_ids.copy()
    while frontier:
        frontier = set(Group.objects.using(alias).filter(parent_id__in=frontier).values_list('pk', flat=True)) - group_ids
        group_ids.update(frontier)
    for doc in Doc.objects.using(alias).filter(group_id__in=group_ids).select_related('group').prefetch_related('tags').iterator(chunk_size=200):
        slugs = [tag.slug[5:] for tag in doc.tags.all() if tag.slug.startswith('tida-') and tag.slug != 'tida-uncatalogued']
        record = Attribution.objects.using(alias).filter(document=doc).select_related('public_type').first()
        wg0_use_case = doc.group.acronym == 'tidawg0' and ('use-cases' in slugs or (record and record.public_type_id and record.public_type.name == 'Use case'))
        if wg0_use_case:
            slugs = ['use-cases']
        if not slugs:
            continue
        name = 'Contribution' if wg0_use_case else names.get(slugs[0], slugs[0].replace('-', ' ').capitalize())
        record, _ = Attribution.objects.using(alias).get_or_create(document=doc)
        if not record.public_type_id or name == 'Contribution':
            record.public_type = Type.objects.using(alias).get_or_create(name=name)[0]
            record.save(using=alias, update_fields=['public_type'])
    for name in names.values():
        Type.objects.using(alias).get_or_create(name=name)


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0036_contribution_contact_affiliation_and_more')]
    operations = [migrations.RunPython(migrate_types, migrations.RunPython.noop)]
