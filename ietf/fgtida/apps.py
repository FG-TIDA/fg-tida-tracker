from django.apps import AppConfig


class FgTidaConfig(AppConfig):
    name = 'ietf.fgtida'
    verbose_name = 'FG-TIDA configuration'

    def ready(self):
        from .admin import configure_admin
        configure_admin()
        from django.db.models.signals import pre_delete, pre_save
        from ietf.group.models import Group, notify_rfceditor_of_group_name_change
        # The inherited RFC Editor notification is not part of FG-TIDA's workflow.
        pre_save.disconnect(notify_rfceditor_of_group_name_change, sender=Group)
        from ietf.doc.models import Document
        from .github_catalog import preserve_deletion
        pre_delete.connect(preserve_deletion, sender=Document, dispatch_uid='fgtida-github-deletion')
