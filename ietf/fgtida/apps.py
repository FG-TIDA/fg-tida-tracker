from django.apps import AppConfig


class FgTidaConfig(AppConfig):
    name = 'ietf.fgtida'
    verbose_name = 'FG-TIDA configuration'

    def ready(self):
        from .admin import configure_admin
        configure_admin()
        from django.db.models.signals import post_delete, pre_delete, pre_save
        from ietf.group.models import Group, notify_rfceditor_of_group_name_change
        # The inherited RFC Editor notification is not part of FG-TIDA's workflow.
        pre_save.disconnect(notify_rfceditor_of_group_name_change, sender=Group)
        from ietf.doc.models import Document
        from .github_catalog import preserve_deletion
        pre_delete.connect(preserve_deletion, sender=Document, dispatch_uid='fgtida-github-deletion')
        from .models import Contribution, compact_contribution_numbers, lock_contribution_numbers
        pre_delete.connect(lock_contribution_numbers, sender=Contribution, dispatch_uid='fgtida-lock-contribution-order')
        post_delete.connect(compact_contribution_numbers, sender=Contribution, dispatch_uid='fgtida-contribution-order')
