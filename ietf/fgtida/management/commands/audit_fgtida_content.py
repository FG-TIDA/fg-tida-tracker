"""Read-only content diagnostics suitable for sharing without account secrets."""
import json
import os
from collections import Counter
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from ietf.fgtida import core
from ietf.fgtida.models import GitHubSource, GitHubRepository
from ietf.fgtida.versioning import parts
from django.core.exceptions import ValidationError


class Command(BaseCommand):
    help='Report FG-TIDA archive/version/import completeness. Does not import, repair or expose account credentials.'

    def handle(self,*args,**options):
        expected=['0010_githubaccount_contribution_draft_repository_url_and_more','0011_documentversion','0012_backfill_document_versions']
        applied=MigrationRecorder(connection).applied_migrations()
        migrations={name:('fgtida',name) in applied for name in expected}
        if not all(migrations.values()):
            raise CommandError('Required FG-TIDA migrations are missing. Run showmigrations fgtida and apply the reviewed migrations before auditing content.')
        counts=Counter()
        for doc in core.documents().prefetch_related('tida_versions').iterator(chunk_size=100):
            counts['documents']+=1
            if not doc.uploaded_filename:
                counts['without_archived_file']+=1
                continue
            counts['with_archived_file']+=1
            available=Path(doc.get_file_name()).is_file()
            if not available:
                counts['missing_current_file_on_disk']+=1
            if available and Path(doc.uploaded_filename).suffix.lower() in {'.md','.rst','.txt'} and not Path(doc.get_file_name()).read_text(encoding='utf-8-sig').strip():
                counts['empty_text_files']+=1
            version=next((row.version for row in doc.tida_versions.all() if row.revision==doc.rev),None)
            if version is None:
                counts['missing_current_version_label']+=1
            else:
                try:
                    parts(version)
                except ValidationError:
                    counts['invalid_current_version_label']+=1
        for name in ['documents','with_archived_file','without_archived_file','missing_current_file_on_disk','empty_text_files','missing_current_version_label','invalid_current_version_label']:
            counts.setdefault(name,0)
        sources=GitHubSource.objects.filter(repository__group__in=core.groups())
        report={'deployed_commit':os.environ.get('RENDER_GIT_COMMIT','local')[:40],
            'migrations':migrations,'content':dict(counts),
            'source_modes':dict(Counter(sources.values_list('sync_mode',flat=True))),
            'repositories_configured':GitHubRepository.objects.filter(group__in=core.groups()).count(),
            'repositories_ever_imported':GitHubRepository.objects.filter(group__in=core.groups(),last_sync__isnull=False).count(),
            'charters':[{'group':group.acronym,'linked':bool(group.charter_id)} for group in core.groups().filter(type_id='wg').exclude(state_id='conclude').order_by('acronym')]}
        self.stdout.write(json.dumps(report,indent=2))
        self.stdout.write('Read-only audit. Reference-only records may be intentional; missing physical files require disk/backup investigation. No account names, emails or credentials are included.')
