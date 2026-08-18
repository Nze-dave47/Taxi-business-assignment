from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
import base64
from django.contrib.auth.models import User
from django.urls import reverse

from .models import UserProfile, Booking, Cab
from .models import RideAudit


class BookingFlowTests(TestCase):
	def setUp(self):
		# Create a passenger and a driver user with profiles
		self.passenger = User.objects.create_user(username='passenger1', password='pass')
		self.driver = User.objects.create_user(username='driver1', password='pass')

		# App signal creates a UserProfile on user creation; update it instead of creating duplicates.
		passenger_profile, _ = UserProfile.objects.get_or_create(user=self.passenger)
		passenger_profile.role = 'PASSENGER'
		passenger_profile.is_available = False
		passenger_profile.save()

		driver_profile, _ = UserProfile.objects.get_or_create(user=self.driver)
		driver_profile.role = 'DRIVER'
		driver_profile.is_available = True
		driver_profile.save()

	def test_passenger_can_request_driver_and_driver_becomes_unavailable(self):
		self.client.login(username='passenger1', password='pass')

		driver_profile = UserProfile.objects.get(user=self.driver)

		resp = self.client.post(reverse('book_ride'), {'driver_id': self.driver.id})
		# Redirects to waiting page
		self.assertIn(resp.status_code, (302, 201))

		booking = Booking.objects.filter(passenger=self.passenger).order_by('-pickup_time').first()
		self.assertIsNotNone(booking)
		self.assertEqual(booking.driver, self.driver)
		self.assertEqual(booking.status, 'PENDING')

		driver_profile.refresh_from_db()
		self.assertFalse(driver_profile.is_available)

	def test_passenger_cancel_after_driver_accepts_creates_passenger_cancelled_audit(self):
		# Passenger requests a driver
		self.client.login(username='passenger1', password='pass')
		resp = self.client.post(reverse('book_ride'), {'driver_id': self.driver.id})
		self.assertIn(resp.status_code, (302, 201))
		booking = Booking.objects.filter(passenger=self.passenger).order_by('-pickup_time').first()
		self.assertIsNotNone(booking)

		# Driver accepts the ride
		self.client.logout()
		self.client.login(username='driver1', password='pass')
		accept_resp = self.client.post(reverse('accept_ride'), {'booking_id': booking.id})
		# Debug output when running tests to capture why acceptance might fail
		print('ACCEPT_RESP_STATUS:', accept_resp.status_code)
		print('ACCEPT_RESP_CONTENT:', getattr(accept_resp, 'content', None))
		self.assertIn(accept_resp.status_code, (200, 302))
		booking.refresh_from_db()
		self.assertEqual(booking.status, 'ACTIVE')

		# Passenger cancels after driver accepted
		self.client.logout()
		self.client.login(username='passenger1', password='pass')
		cancel_url = reverse('cancel_booking', args=[booking.id])
		cancel_resp = self.client.post(cancel_url, follow=True)
		self.assertIn(cancel_resp.status_code, (200, 302))
		booking.refresh_from_db()
		self.assertEqual(booking.status, 'CANCELLED')

		# Ensure driver was freed
		driver_profile = UserProfile.objects.get(user=self.driver)
		driver_profile.refresh_from_db()
		self.assertTrue(driver_profile.is_available)

		# Ensure a passenger_cancelled RideAudit was created with proper details
		audit_exists = RideAudit.objects.filter(action='passenger_cancelled', booking=booking).exists()
		self.assertTrue(audit_exists)

	def test_driver_toggle_availability(self):
		self.client.login(username='driver1', password='pass')

		profile = UserProfile.objects.get(user=self.driver)
		# toggle availability (POST)
		resp = self.client.post(reverse('toggle_availability'))
		# Support both JSON API (200) and browser redirect (302)
		self.assertIn(resp.status_code, (200, 302))

		profile.refresh_from_db()
		# toggled to False
		self.assertFalse(profile.is_available)


class ProfileManagementTests(TestCase):
	def setUp(self):
		self.client = Client()
		self.user = User.objects.create_user(username='alice', password='password123', first_name='Alice')
		# Ensure profile exists (app signal may auto-create)
		profile, _ = UserProfile.objects.get_or_create(user=self.user)
		profile.role = 'PASSENGER'
		profile.save()

	def test_update_name_and_upload_picture(self):
		self.client.login(username='alice', password='password123')

		url = reverse('profile_management')

		# 1x1 PNG pixel (valid image) base64-decoded
		# 1x1 PNG pixel (valid image) base64-decoded
		tiny_png = base64.b64decode(
			'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQMAAAAl21bKAAAAA1BMVEUAAACnej3aAAAAAXRSTlMAQObYZgAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII='
		)
		img = SimpleUploadedFile('avatar.png', tiny_png, content_type='image/png')
		# Attach file in the POST data to ensure multipart is handled reliably in test client
		data = {'first_name': 'Alicia', 'last_name': 'Keys', 'profile_picture': img}
		response = self.client.post(url, data=data, follow=True)
		self.assertEqual(response.status_code, 200)

		user = User.objects.get(pk=self.user.pk)
		self.assertEqual(user.first_name, 'Alicia')

		profile = UserProfile.objects.get(user=user)
		self.assertTrue(profile.profile_picture)

	def test_password_change(self):
		self.client.login(username='alice', password='password123')
		url = reverse('profile_management')
		data = {
			'old_password': 'password123',
			'new_password1': 'newpass456',
			'new_password2': 'newpass456',
		}
		response = self.client.post(url, data=data, follow=True)
		self.assertEqual(response.status_code, 200)

		user = User.objects.get(pk=self.user.pk)
		self.assertTrue(user.check_password('newpass456'))


class AuthenticationFlowTests(TestCase):
	def test_passenger_registration_creates_passenger_profile_and_logs_in(self):
		response = self.client.post(reverse('register'), {
			'username': 'newpassenger',
			'first_name': 'New',
			'last_name': 'Passenger',
			'email': 'passenger@example.com',
			'password': 'StrongPass123!',
			'password_confirm': 'StrongPass123!',
		})
		self.assertRedirects(response, reverse('dashboard'))
		user = User.objects.get(username='newpassenger')
		self.assertTrue(user.check_password('StrongPass123!'))
		self.assertEqual(user.profile.role, 'PASSENGER')
		self.assertFalse(user.profile.is_available)

	def test_hidden_driver_registration_creates_driver_profile_and_cab(self):
		response = self.client.post(reverse('driver_register'), {
			'username': 'newdriver',
			'first_name': 'New',
			'last_name': 'Driver',
			'email': 'driver@example.com',
			'car_model': 'Toyota Camry',
			'password': 'StrongPass123!',
			'password_confirm': 'StrongPass123!',
		})
		self.assertRedirects(response, reverse('dashboard'))
		user = User.objects.get(username='newdriver')
		self.assertEqual(user.profile.role, 'DRIVER')
		self.assertTrue(user.profile.is_available)
		self.assertTrue(Cab.objects.filter(driver_name='New Driver', car_model='Toyota Camry').exists())

	def test_registration_allows_spaces_in_username(self):
		username = 'Passenger With Spaces'
		response = self.client.post(reverse('register'), {
			'username': username,
			'first_name': 'Space',
			'last_name': 'Passenger',
			'email': 'spaces@example.com',
			'password': 'StrongPass123!',
			'password_confirm': 'StrongPass123!',
		})
		self.assertRedirects(response, reverse('dashboard'))
		self.assertTrue(User.objects.filter(username=username).exists())
