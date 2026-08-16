import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import DatabaseError, OperationalError
from django.utils import timezone

from rides.models import Booking, UserProfile


class Command(BaseCommand):
    help = "Continuously auto-cancel expired pending bookings and release assigned drivers"

    def handle(self, *args, **options):
        while True:
            try:
                cutoff_time = timezone.now() - timedelta(minutes=2)
                expired_bookings = Booking.objects.filter(
                    status="PENDING",
                    pickup_time__lt=cutoff_time,
                ).select_related("driver")

                for booking in expired_bookings:
                    booking.status = "CANCELLED"
                    booking.save(update_fields=["status"])

                    if booking.driver_id is not None:
                        try:
                            driver_profile = UserProfile.objects.get(user_id=booking.driver_id)
                        except UserProfile.DoesNotExist:
                            driver_profile = None

                        if driver_profile is not None:
                            driver_profile.is_available = True
                            driver_profile.save(update_fields=["is_available"])

                    timestamp = timezone.localtime(timezone.now()).strftime("%H:%M:%S")
                    self.stdout.write(
                        self.style.WARNING(
                            f"Auto-cancelled expired booking #{booking.id} at {timestamp}"
                        )
                    )

            except (OperationalError, DatabaseError) as exc:
                self.stdout.write(
                    self.style.ERROR(f"Database temporarily unavailable: {exc}. Waiting before retrying...")
                )

            time.sleep(5)
