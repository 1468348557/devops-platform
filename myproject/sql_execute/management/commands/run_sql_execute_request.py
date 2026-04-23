from django.core.management.base import BaseCommand, CommandError

from sql_execute.views import _sql_execute_worker


class Command(BaseCommand):
    help = "Run SQL execution request worker in detached process."

    def add_arguments(self, parser):
        parser.add_argument("request_id", type=int)

    def handle(self, *args, **options):
        request_id = options["request_id"]
        if request_id <= 0:
            raise CommandError("request_id 必须为正整数")
        _sql_execute_worker(request_id)
