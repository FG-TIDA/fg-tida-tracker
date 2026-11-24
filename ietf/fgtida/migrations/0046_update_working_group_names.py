from django.db import migrations


NAMES = [
    (0, 'Use Cases, Taxonomy, and Terminology', 'uct'),
    (1, 'Continuous and Dynamic Remote Attestation for Agentic AI', 'cdra'),
    (5, 'Implementation Conditions and Adoption Pathways for Trusted Agentic AI', 'icap'),
]
REPLACEMENTS = [
    ('Use Cases and Terminology', NAMES[0][1]),
    ('Continuous and Dynamic Attestation for Agentic AI', NAMES[1][1]),
    ('CD3A', 'CDRA'),
    ('WG5: ICAP', 'WG5: ' + NAMES[2][1] + ' (ICAP)'),
]


def update_names(apps, schema_editor):
    alias = schema_editor.connection.alias
    Group = apps.get_model('group', 'Group')
    Config = apps.get_model('fgtida', 'WorkingGroupConfiguration')
    Session = apps.get_model('meeting', 'Session')
    root = Group.objects.using(alias).filter(acronym='tida').first()
    if not root:
        return
    for number, name, acronym in NAMES:
        config = Config.objects.using(alias).filter(number=number).first()
        group = Group.objects.using(alias).get(pk=config.group_id) if config else Group.objects.using(alias).filter(parent=root, acronym=f'tidawg{number}').first()
        if group is None:
            group = Group.objects.using(alias).create(parent=root, acronym=f'tidawg{number}', name=name, type_id='wg', state_id='active')
        description = group.description or ''
        for old, new in REPLACEMENTS:
            description = description.replace(old, new)
        Group.objects.using(alias).filter(pk=group.pk).update(name=name, description=description)
        Config.objects.using(alias).update_or_create(group_id=group.pk, defaults={'number': number, 'topic_acronym': acronym})
    # Change live agenda text, preserving session IDs, schedules and materials.
    # Uploaded documents, archive histories and GitHub repository slugs stay intact.
    for session in Session.objects.using(alias).filter(meeting__type_id='tida').iterator():
        fields = {}
        for field in ['name', 'agenda_note', 'agenda_presenters']:
            before = getattr(session, field) or ''
            after = before
            for old, new in REPLACEMENTS:
                after = after.replace(old, new)
            if before != after:
                fields[field] = after
        if fields:
            Session.objects.using(alias).filter(pk=session.pk).update(**fields)


class Migration(migrations.Migration):
    dependencies = [('fgtida', '0045_rapporteur_role')]
    operations = [migrations.RunPython(update_names, migrations.RunPython.noop)]
