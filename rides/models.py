from django.contrib.auth.models import User
from django.db import models


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ("DRIVER", "Driver"),
        ("PASSENGER", "Passenger"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_available = models.BooleanField(default=True)
    profile_picture = models.ImageField(upload_to='profile_pics/', null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.role == "PASSENGER":
            self.is_available = False
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class Cab(models.Model):
    STATUS_CHOICES = [
        ("AVAILABLE", "AVAILABLE"),
        ("ON_TRIP", "ON_TRIP"),
    ]

    driver_name = models.CharField(max_length=100)
    car_model = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="AVAILABLE",
    )

    def __str__(self):
        return f"{self.driver_name} - {self.car_model}"


class Booking(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "PENDING"),
        ("ACTIVE", "ACTIVE"),
        ("COMPLETED", "COMPLETED"),
        ("CANCELLED", "CANCELLED"),
    ]

    passenger = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="passenger_bookings",
        null=True,
        blank=True,
    )
    driver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="driver_bookings",
        null=True,
        blank=True,
    )
    cab = models.ForeignKey(Cab, on_delete=models.CASCADE, related_name="bookings", null=True, blank=True)
    pickup_time = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )
    driver_completed = models.BooleanField(default=False)
    passenger_completed = models.BooleanField(default=False)

    def __str__(self):
        passenger_name = self.passenger.username if self.passenger else "Unassigned"
        driver_name = self.driver.username if self.driver else "Unassigned"
        return f"Booking #{self.id} - Passenger: {passenger_name} | Driver: {driver_name}"


class RideAudit(models.Model):
    """Simple audit log for ride-related actions.

    This model records important actions (booking created, availability toggled, accept/decline)
    for quick inspection in the admin UI.
    """

    timestamp = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=150)
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="ride_audits")
    actor_profile = models.ForeignKey(UserProfile, null=True, blank=True, on_delete=models.SET_NULL)
    booking = models.ForeignKey(Booking, null=True, blank=True, on_delete=models.SET_NULL, related_name="audits")
    details = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        who = self.actor.username if self.actor else "System"
        return f"{self.timestamp.isoformat()} - {self.action} by {who}"
