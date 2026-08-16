from django.apps import AppConfig
from django.db.models.signals import post_save
from django.dispatch import receiver


class RidesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rides'

    def ready(self):
        from django.contrib.auth.models import User

        from .models import UserProfile

        @receiver(post_save, sender=User)
        def create_user_profile(sender, instance, created, **kwargs):
            if created:
                UserProfile.objects.get_or_create(user=instance, defaults={'role': 'PASSENGER'})
