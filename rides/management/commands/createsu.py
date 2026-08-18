from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Creates the deployment superuser when no superuser exists.'

    def handle(self, *args, **options):
        user_model = get_user_model()

        if user_model.objects.filter(is_superuser=True).exists():
            self.stdout.write(
                self.style.WARNING('A superuser already exists; no account was created.')
            )
            return

        user_model.objects.create_superuser(
            username='Nzedave47',
            email='admin@example.com',
            password='13807',
        )
        self.stdout.write(
            self.style.SUCCESS('Superuser "Nzedave47" created successfully.')
        )
