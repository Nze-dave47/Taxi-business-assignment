import csv
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from rides.models import RideAudit


class Command(BaseCommand):
    help = 'Export recent RideAudit entries to CSV. Use --days to limit.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7, help='How many days of audits to export')
        parser.add_argument('--limit', type=int, default=1000, help='Max number of rows to export')
        parser.add_argument('--output', type=str, default='-', help='Output file path (default stdout)')

    def handle(self, *args, **options):
        days = options['days']
        limit = options['limit']
        output = options['output']

        since = datetime.utcnow() - timedelta(days=days)

        qs = RideAudit.objects.filter(timestamp__gte=since).select_related('actor', 'booking').order_by('-timestamp')[:limit]

        fieldnames = ['timestamp', 'action', 'actor', 'booking_id', 'details']

        if output == '-':
            writer = csv.DictWriter(self.stdout, fieldnames=fieldnames)
            writer.writeheader()
            for a in qs:
                writer.writerow({
                    'timestamp': a.timestamp.isoformat(),
                    'action': a.action,
                    'actor': a.actor.username if a.actor else '',
                    'booking_id': a.booking.id if a.booking else '',
                    'details': a.details,
                })
        else:
            with open(output, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for a in qs:
                    writer.writerow({
                        'timestamp': a.timestamp.isoformat(),
                        'action': a.action,
                        'actor': a.actor.username if a.actor else '',
                        'booking_id': a.booking.id if a.booking else '',
                        'details': a.details,
                    })
            self.stdout.write(self.style.SUCCESS(f'Wrote {len(qs)} audits to {output}'))
