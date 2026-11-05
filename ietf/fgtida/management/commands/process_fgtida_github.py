from django.core.management.base import BaseCommand
from ietf.fgtida.github_jobs import run_jobs


class Command(BaseCommand):
    help='Process bounded, durable GitHub synchronization/provisioning jobs. Writes require explicit configuration.'

    def add_arguments(self,parser):
        parser.add_argument('--limit',type=int,default=10)

    def handle(self,*args,**options):
        self.stdout.write(f'Completed {run_jobs(min(max(options["limit"],1),50))} GitHub jobs. Disabled writes remain pending.')
