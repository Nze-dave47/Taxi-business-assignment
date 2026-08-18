from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = 'Keeps only the configured deployment superuser.'

    def handle(self, *args, **options):
        User = get_user_model()
        username = 'Nze-dave47'
        email = 'nzedave43@gmail.com'
        password = '130807'

        other_superusers = (
            User.objects.filter(is_superuser=True)
            .exclude(username=username)
        )
        deleted_count = other_superusers.count()
        other_superusers.delete()
        if deleted_count:
            self.stdout.write(
                self.style.WARNING(
                    f'Removed {deleted_count} existing superuser account(s).'
                )
            )

        user, created = User.objects.get_or_create(username=username)
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        action = 'created' if created else 'updated'
        self.stdout.write(self.style.SUCCESS(f'Superuser "{username}" {action} successfully.'))
