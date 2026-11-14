import re

from django.db import migrations, models


TOPIC_NUMBER = re.compile(r'^\d+(?:\.\d+)+(?:[.)])?(?=\s)')
PRESENTER = re.compile(r'^lead\s*/\s*presenter:\s*(.*)', re.IGNORECASE)
DOCUMENT = re.compile(r'^supporting documents?:\s*(.*)', re.IGNORECASE)
PLACEHOLDER = re.compile(r'(?:TBD|TBC)[.!]?', re.IGNORECASE)


def split_agenda_notes(apps, schema_editor):
    session_model = apps.get_model('meeting', 'Session')
    sessions = session_model.objects.using(schema_editor.connection.alias)
    notes = sessions.filter(
        meeting__type_id='tida', agenda_presenters='',
    ).exclude(agenda_note='')
    for session in notes.iterator():
        event_lines = []
        presenters = []
        topic_number = ''
        reading_presenter = False
        converted = False
        for line in session.agenda_note.splitlines():
            stripped = line.strip()
            topic = TOPIC_NUMBER.match(stripped)
            presenter = PRESENTER.match(stripped)
            document = DOCUMENT.match(stripped)
            if topic:
                topic_number = topic.group().rstrip('.)')
            if presenter:
                name = presenter[1]
                presenters.append(f'{topic_number} {name}' if topic_number else name)
                reading_presenter = True
                converted = True
                continue
            if not stripped or topic or document:
                reading_presenter = False
            if reading_presenter:
                presenters[-1] = ' / '.join(filter(None, (presenters[-1].strip(), stripped)))
            elif document and PLACEHOLDER.fullmatch(document[1].strip()):
                converted = True
            else:
                event_lines.append(line)
        if converted:
            event_note = re.sub(r'\n{3,}', '\n\n', '\n'.join(event_lines).strip())
            sessions.filter(pk=session.pk).update(
                agenda_note=event_note, agenda_presenters='\n'.join(filter(None, presenters)),
            )


class Migration(migrations.Migration):
    dependencies = [('meeting', '0024_meeting_category')]

    operations = [
        migrations.AlterField(model_name='session', name='agenda_note', field=models.TextField(blank=True)),
        migrations.AddField(model_name='session', name='agenda_presenters',
                            field=models.TextField(blank=True, db_default='')),
        migrations.RunPython(split_agenda_notes),
    ]
