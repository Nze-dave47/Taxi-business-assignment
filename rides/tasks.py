from .models import Booking, UserProfile


def complete_trip_task(booking_id):
    try:
        booking = Booking.objects.get(id=booking_id)
    except Booking.DoesNotExist:
        return

    booking.status = "COMPLETED"
    booking.save(update_fields=["status"])

    if booking.driver_id is not None:
        try:
            driver_profile = UserProfile.objects.get(user_id=booking.driver_id)
        except UserProfile.DoesNotExist:
            driver_profile = None

        if driver_profile is not None:
            driver_profile.is_available = True
            driver_profile.save(update_fields=["is_available"])
