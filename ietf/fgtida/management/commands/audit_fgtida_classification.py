"""Plan/apply source-evidence corrections without downloading or replacing files."""
import copy
import json
from urllib.parse import urlsplit
from pathlib import PurePosixPath
from collections import Counter
from django.core.management.base import BaseCommand
from django.db import transaction
from ietf.group.models import Group
from ietf.fgtida import core
from ietf.fgtida.models import GitHubRepository, GitHubSource
from ietf.fgtida.classification import SOURCE_RULES, resolve, refresh, refresh_document


class Command(BaseCommand):
    help = 'Audit source classifications; --apply corrects only automatic metadata, preserving archives and manual overrides.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        apply = options['apply']
        report = {'mode': 'apply' if apply else 'plan', 'repository_changes': [], 'document_changes': [], 'preserved': Counter()}
        category_tags = ['tida-'+slug for slug, _ in core.CATEGORIES]
        for saved in GitHubRepository.objects.filter(group__in=core.groups()).select_related('account', 'group'):
            repo = copy.copy(saved)
            rule = SOURCE_RULES.get(repo.full_name.lower())
            if rule:
                acronym, category, group_evidence, category_evidence = rule
                old_groups = {'tida', acronym}
                if repo.full_name.lower() == 'fg-tida/use-cases':
                    old_groups.add('tidawg0')
                if repo.full_name.lower() == 'fg-tida/themes':
                    old_groups.add('incubation')
                if not repo.group_evidence and repo.group.acronym in old_groups:
                    repo.group = Group.objects.get(acronym=acronym)
                    repo.group_evidence = group_evidence
                if not repo.category_evidence and repo.category in {'', category, 'resources', 'deliverables'}:
                    repo.category, repo.category_evidence = category, category_evidence
            changed = [field for field in ['group_id', 'category', 'group_evidence', 'category_evidence'] if getattr(repo, field) != getattr(saved, field)]
            if changed:
                report['repository_changes'].append({'repository': repo.full_name, 'group': repo.group.acronym,
                    'category': repo.category or None, 'fields': changed})
                if apply:
                    repo.save(update_fields=['group' if field == 'group_id' else field for field in changed])
            for source in GitHubSource.objects.filter(repository=saved).select_related('document', 'document__group'):
                doc = source.document
                if not doc or source.sync_mode != 'automatic' or (hasattr(doc, 'tida_work_item') and doc.tida_work_item.sync_mode != 'automatic'):
                    report['preserved']['manual_excluded_or_deleted'] += 1
                    continue
                group, category, metadata = resolve(repo, source.source_key, source.source_url, source.source_metadata)
                old_tags = set(doc.tags.filter(slug__in=category_tags).values_list('slug', flat=True))
                expected = {'tida-'+category} if category else set()
                if doc.group_id != group.pk or old_tags != expected:
                    report['document_changes'].append({'document': doc.name, 'source': source.source_url,
                        'from_group': doc.group.acronym, 'to_group': group.acronym,
                        'from_categories': sorted(old_tags), 'to_categories': sorted(expected)})
                if apply:
                    refresh(source, group, category, metadata)
        report['preserved'] = dict(report['preserved'])
        unlinked = core.documents().filter(github_sources__isnull=True).select_related('group').select_for_update(of=('self',))
        report['unlinked_catalogue_records'] = unlinked.count()
        report['legacy_document_changes'] = []
        for doc in unlinked:
            parsed = urlsplit(doc.external_url or '')
            parts = parsed.path.strip('/').split('/')
            if parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or len(parts) < 3:
                continue
            rule = SOURCE_RULES.get('/'.join(parts[:2]).lower())
            from ietf.fgtida.github_catalog import SUPPORT, SUFFIXES
            issue_url = len(parts) == 4 and parts[2] == 'issues' and parts[3].isdigit()
            file_url = len(parts) >= 5 and parts[2] == 'blob' and PurePosixPath(parsed.path).suffix.lower() in SUFFIXES and PurePosixPath(parsed.path).name.lower() not in SUPPORT
            if not (issue_url or file_url):
                continue
            if not rule or doc.docevent_set.filter(by__user__isnull=False).exists():
                continue
            if hasattr(doc, 'tida_work_item') and doc.tida_work_item.sync_mode != 'automatic':
                continue
            acronym, category, _, _ = rule
            if '/files/' in parsed.path or parts[2] == 'user-attachments':
                category = ''
            group = Group.objects.get(acronym=acronym)
            actual = set(doc.tags.filter(slug__in=category_tags).values_list('slug', flat=True))
            if doc.group_id != group.pk or actual != ({'tida-'+category} if category else set()):
                report['legacy_document_changes'].append({'document':doc.name,'source':doc.external_url,'to_group':acronym,'category':category or None})
                if apply:
                    refresh_document(doc, group, category, doc.external_url)
        self.stdout.write(json.dumps(report, indent=2))
        self.stdout.write('No source files, versions, approvals, account data, emails or GitHub repositories were changed. Unlinked/editor-managed documents require individual review.')
