import io
import zipfile
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.core.exceptions import ValidationError
from django.test import TestCase, SimpleTestCase, override_settings
from ietf.doc.models import Document
from ietf.group.models import Group
from . import core, tests as native_tests
from .models import WorkingGroupConfiguration
from .files import extract_text


@override_settings(FGTIDA_REQUIRE_ADMIN_MFA=False)
class NavigationTests(TestCase):
    setUp = native_tests.NativeDatatrackerTests.setUp

    def document(self, name, group, category=None, kind='tida'):
        doc = Document(name=name, title=name, group=group, type_id=kind)
        core.save_document(doc, core.actor_for(self.user), 'Test archive', data=b'Archived content', filename='original.txt')
        if category:
            doc.tags.add('tida-'+category)
        return doc

    def test_menu_and_group_sections(self):
        wg = Group.objects.get(acronym='tidawg1')
        WorkingGroupConfiguration.objects.update_or_create(group=wg, defaults={'number':1, 'topic_acronym':'cdra'})
        page = self.client.get('/')
        for label in ['Groups', 'WG1 (CDRA)', 'No working group yet', 'Liaison statements inputs', 'Liaison statements outputs', 'Floor plan']:
            self.assertContains(page, label)
        for label in ['WGs Charters', 'Request a session', 'Session requests', 'Meeting items', 'Incubation - approval']:
            self.assertNotContains(page, label)
        menu = page.content.decode().split('>Meetings</a>', 1)[1].split('</ul>', 1)[0]
        self.assertEqual(menu.count('dropdown-divider'), 2)
        self.assertNotIn('>Minutes<', menu)
        self.assertNotIn('>Materials<', menu)
        self.assertNotIn('>Recordings<', menu)
        doc = self.document('wg1-deliverable', wg, 'deliverables')
        other = self.document('other-group-deliverable', Group.objects.get(acronym='tidawg2'), 'deliverables')
        page = self.client.get('/group/tidawg1/deliverables/?category=themes&group=tidawg2')
        self.assertContains(page, doc.title)
        self.assertNotContains(page, other.title)
        self.assertNotContains(page, '>Milestones<')
        charter = self.document('wg1-charter', wg, kind='charter')
        self.assertContains(self.client.get('/group/tidawg1/charter/'), charter.title)
        self.assertNotContains(self.client.get('/group/tidawg2/charter/'), charter.title)

    def test_use_case_migration_preserves_files_and_revision_history(self):
        doc = self.document('use-case-to-move', self.root, 'use-cases')
        filename = doc.get_file_name()
        original = Path(filename).read_bytes()
        versions = list(doc.tida_versions.values_list('revision','version','recorded'))
        history = list(doc.history_set.values_list('pk', flat=True))
        migrate = import_module('ietf.fgtida.migrations.0020_assign_use_cases_to_wg0').move_use_cases
        apps = MigrationExecutor(connection).loader.project_state([('fgtida', '0019_alter_outgoingmail_html_body')]).apps
        migrate(apps, SimpleNamespace(connection=connection))
        doc.refresh_from_db()
        self.assertEqual(doc.group.acronym, 'tidawg0')
        self.assertEqual(Path(doc.get_file_name()).read_bytes(), original)
        self.assertEqual(versions, list(doc.tida_versions.values_list('revision','version','recorded')))
        self.assertEqual(history, list(doc.history_set.values_list('pk', flat=True)))
        count = doc.docevent_set.count()
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(doc.docevent_set.count(), count)
        self.assertContains(self.client.get('/group/tidawg0/use-cases/'), doc.title)


class LiaisonPackageTests(SimpleTestCase):
    def test_word_zip_preserves_original_and_extracts_reading_text(self):
        word = io.BytesIO()
        with zipfile.ZipFile(word, 'w') as archive:
            archive.writestr('word/document.xml', '<document><p>Liaison statement</p></document>')
        from .source_packages import pack
        original = pack({'statement.docx': word.getvalue(), 'annex.txt': b'Annex content'})
        self.assertIn('Liaison statement', extract_text(original, 'liaison.zip'))
        self.assertIn('Annex content', extract_text(original, 'liaison.zip'))
        with self.assertRaises(ValidationError):
            extract_text(pack({'statement.docx': word.getvalue(), 'run.exe': b'unsafe'}), 'liaison.zip')
