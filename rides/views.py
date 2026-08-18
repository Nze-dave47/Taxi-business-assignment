import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.exceptions import FieldError, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from django_q.tasks import schedule
from django_q.models import Schedule
from django.views.decorators.csrf import csrf_exempt
from .models import Booking, Cab, UserProfile
from .models import RideAudit
from .tasks import complete_trip_task 
from .logger import logger
from .forms import UserForm, UserProfileForm, SimplePasswordChangeForm
from io import BytesIO
from django.http.multipartparser import MultiPartParser
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.utils.http import url_has_allowed_host_and_scheme


def login_view(request):
    """Authenticate a user and send them to the taxi dashboard."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == 'POST' and form.is_valid():
        auth_login(request, form.get_user())
        messages.success(request, f'Welcome back, {request.user.username}.')
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
            return redirect(next_url)
        return redirect('dashboard')

    return render(request, 'registration/login.html', {'form': form})


def _register_user(request, role, template_name):
    """Create a role-specific user account from a registration form."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    context = {'values': {}}
    if request.method != 'POST':
        return render(request, template_name, context)

    username = request.POST.get('username', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name = request.POST.get('last_name', '').strip()
    email = request.POST.get('email', '').strip()
    password = request.POST.get('password', '')
    password_confirm = request.POST.get('password_confirm', '')
    car_model = request.POST.get('car_model', '').strip()
    errors = []
    context['values'] = request.POST

    if not all((username, first_name, last_name, email, password, password_confirm)):
        errors.append('Please complete every required field.')
    if username and not username.strip():
        errors.append('Username cannot contain only spaces.')
    if len(username) > 150:
        errors.append('Username must be 150 characters or fewer.')
    if password != password_confirm:
        errors.append('Passwords do not match.')
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors.append('Enter a valid email address.')
    if username and User.objects.filter(username__iexact=username).exists():
        errors.append('That username is already in use.')
    if email and User.objects.filter(email__iexact=email).exists():
        errors.append('An account already uses that email address.')

    candidate = User(username=username, first_name=first_name, last_name=last_name, email=email)
    if not errors:
        try:
            # Django's default username validator disallows spaces and some text
            # characters. The database still enforces the 150-character limit.
            candidate.full_clean(exclude=['username', 'password'])
            validate_password(password, user=candidate)
        except ValidationError as exc:
            errors.extend(exc.messages)

    if errors:
        context['errors'] = errors
        return render(request, template_name, context)

    with transaction.atomic():
        user = User.objects.create_user(
            username=username, first_name=first_name, last_name=last_name,
            email=email, password=password,
        )
        # The existing User post-save signal creates a passenger profile; update its role safely.
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'role': role})
        profile.role = role
        profile.is_available = role == 'DRIVER'
        profile.save(update_fields=['role', 'is_available'])

        # Cab has no User foreign key, so it records the driver's display name.
        if role == 'DRIVER' and car_model:
            Cab.objects.create(
                driver_name=user.get_full_name() or user.username,
                car_model=car_model,
                status='AVAILABLE',
            )

    auth_user = authenticate(request, username=username, password=password)
    if auth_user is not None:
        auth_login(request, auth_user)
        messages.success(request, 'Your account has been created successfully.')
        return redirect('dashboard')

    messages.success(request, 'Your account has been created. Please sign in.')
    return redirect('login')


def passenger_register_view(request):
    """Public registration endpoint: every account created here is a passenger."""
    return _register_user(request, 'PASSENGER', 'registration/passenger_register.html')


def driver_register_view(request):
    """Unlisted driver onboarding endpoint, intentionally absent from public navigation."""
    return _register_user(request, 'DRIVER', 'registration/driver_register.html')


def get_driver_profile(user):
    """Return the logged-in driver's profile safely, supporting either profile-style or user-style relationships."""
    if not user or not getattr(user, "is_authenticated", False):
        return None

    for attr in ("userprofile", "profile"):
        profile = getattr(user, attr, None)
        if profile is not None:
            return profile

    try:
        return UserProfile.objects.get(user=user)
    except UserProfile.DoesNotExist:
        return None


def get_driver_bookings(user, status):
    """Return bookings for the logged-in driver using the correct driver reference."""
    driver_profile = get_driver_profile(user)
    if not driver_profile:
        return Booking.objects.none()

    try:
        return Booking.objects.filter(driver=driver_profile, status=status)
    except (FieldError, TypeError, ValueError):
        return Booking.objects.filter(driver=user, status=status)


@require_GET
def available_cabs_view(request):
    cabs = Cab.objects.filter(status="AVAILABLE").values("id", "driver_name", "car_model")
    return JsonResponse(list(cabs), safe=False)


def check_notifications(request):
    """Return pending bookings for the logged-in driver using the correct profile reference."""
    if not request.user.is_authenticated:
        return JsonResponse({'bookings': []}, status=403)

    driver_profile = get_driver_profile(request.user)
    if not driver_profile or getattr(driver_profile, 'role', None) != 'DRIVER':
        return JsonResponse({'bookings': []}, status=403)

    pending_bookings = get_driver_bookings(request.user, status='PENDING').select_related('passenger')

    data = []
    for booking in pending_bookings:
        passenger = booking.passenger
        passenger_name = getattr(passenger, 'username', None)
        if passenger_name is None and hasattr(passenger, 'user'):
            passenger_name = passenger.user.username
        data.append({
            'id': booking.id,
            'passenger': passenger_name or 'Unknown passenger',
        })

    return JsonResponse({'bookings': data})


@require_POST
def book_cab_view(request):
    # Robust parsing: accept JSON when content-type is JSON or body looks like JSON,
    # otherwise treat as form POST data.
    try:
        body_text = request.body.decode('utf-8') if request.body else ''
    except Exception:
        body_text = ''

    if body_text.startswith('{') or request.content_type == 'application/json':
        try:
            data = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST or {}

    cab_id = data.get("cab_id")
    user_id = data.get("user_id")

    if cab_id is None or user_id is None:
        return JsonResponse({"error": "Both cab_id and user_id are required."}, status=400)

    try:
        with transaction.atomic():
            cab = Cab.objects.select_for_update().get(id=cab_id)

            if cab.status != "AVAILABLE":
                return JsonResponse({"error": "The cab is already taken."}, status=400)

            try:
                rider = User.objects.get(id=user_id)
            except User.DoesNotExist:
                return JsonResponse({"error": "User not found."}, status=404)

            cab.status = "ON_TRIP"
            cab.save(update_fields=["status"])

            booking = Booking.objects.create(passenger=rider, cab=cab, status="PENDING")
            schedule(
                'rides.tasks.complete_trip_task',
                booking.id,
                schedule_type='O',
                minutes=2,
            )

            return JsonResponse(
                {
                    "message": "Cab booked successfully.",
                    "booking_id": booking.id,
                    "cab_id": cab.id,
                },
                status=201,
            )
    except Cab.DoesNotExist:
        return JsonResponse({"error": "Cab not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid cab_id or user_id."}, status=400)


@require_POST
def book_driver_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Passenger profile not found."}, status=404)

    if profile.role != "PASSENGER":
        return JsonResponse({"error": "Only passengers can book drivers."}, status=403)

    # Robust parsing: accept JSON when content-type is JSON or body looks like JSON,
    # otherwise treat as form POST data.
    try:
        body_text = request.body.decode('utf-8') if request.body else ''
    except Exception:
        body_text = ''

    if body_text.startswith('{') or request.content_type == 'application/json':
        try:
            data = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST or {}

    driver_id = data.get("driver_id")
    if driver_id is None:
        return JsonResponse({"error": "driver_id is required."}, status=400)

    try:
        with transaction.atomic():
            driver_profile = UserProfile.objects.select_for_update().get(user_id=driver_id)

            if driver_profile.role != "DRIVER" or not driver_profile.is_available:
                return JsonResponse(
                    {"error": "This driver is currently unavailable or already on a trip."},
                    status=400,
                )

            driver_profile.is_available = False
            driver_profile.save(update_fields=["is_available"])

            booking = Booking.objects.create(
                passenger=request.user,
                driver=driver_profile.user,
                status="PENDING",
            )

            return JsonResponse(
                {
                    "message": "Driver booked successfully.",
                    "booking_id": booking.id,
                    "driver_id": driver_profile.user.id,
                },
                status=201,
            )
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid driver_id."}, status=400)


@require_POST
def toggle_availability_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)

    if profile.role != "DRIVER":
        return JsonResponse({"error": "Only drivers can toggle availability."}, status=403)

    # Robustly parse JSON only when content type is JSON; otherwise use POST data.
    data = {}
    if request.content_type == 'application/json' and request.body:
        try:
            data = json.loads(request.body.decode('utf-8'))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST or {}

    if "is_available" in data:
        # request.POST returns strings; coerce booleans from common representations
        new_status = data.get("is_available")
        if isinstance(new_status, str):
            new_status = new_status.lower() in ("1", "true", "yes", "on")
        if not isinstance(new_status, bool):
            return JsonResponse({"error": "is_available must be true or false."}, status=400)
    else:
        new_status = not profile.is_available

    profile.is_available = new_status
    profile.save(update_fields=["is_available"])

    status_label = "Available" if profile.is_available else "Unavailable"
    logger.info('Availability toggled', extra={'driver': getattr(request.user, 'username', None), 'is_available': profile.is_available})
    try:
        RideAudit.objects.create(
            action='availability_toggled',
            actor=request.user,
            actor_profile=profile,
            details=f'Availability set to {profile.is_available}'
        )
    except Exception:
        logger.exception('Failed to create RideAudit for availability change')
    # If this is an API/JSON request, return JSON; otherwise redirect back to the driver portal
    accept = request.META.get('HTTP_ACCEPT', '')
    is_json_request = (request.content_type == 'application/json') or ('application/json' in accept)
    if is_json_request:
        return JsonResponse({"message": "Availability updated.", "status": status_label, "is_available": profile.is_available})

    messages.success(request, f'Availability updated — {status_label}')
    return redirect('driver_portal')


@require_POST
def request_ride_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        passenger_profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Passenger profile not found."}, status=404)

    if passenger_profile.role != "PASSENGER":
        return JsonResponse({"error": "Only passengers can request rides."}, status=403)

    # Robust parsing: accept JSON when content-type is JSON or body looks like JSON,
    # otherwise treat as form POST data.
    try:
        body_text = request.body.decode('utf-8') if request.body else ''
    except Exception:
        body_text = ''

    if body_text.startswith('{') or request.content_type == 'application/json':
        try:
            data = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST or {}

    driver_id = data.get("driver_id")
    if driver_id is None:
        return JsonResponse({"error": "driver_id is required."}, status=400)

    try:
        with transaction.atomic():
            driver_profile = UserProfile.objects.select_for_update().get(user_id=driver_id)

            if driver_profile.role != "DRIVER":
                return JsonResponse(
                    {"error": "This user is not a driver."},
                    status=400,
                )

            if Booking.objects.filter(driver=driver_profile.user, status__in=["PENDING", "ACTIVE"]).exists():
                return JsonResponse(
                    {"error": "This driver is currently unavailable or already on a trip."},
                    status=400,
                )

            driver_profile.is_available = False
            driver_profile.save(update_fields=["is_available"])

            booking = Booking.objects.create(
                passenger=request.user,
                driver=driver_profile.user,
                status="PENDING",
            )

            return JsonResponse(
                {
                    "message": "Ride request sent. Waiting for driver approval.",
                    "booking_id": booking.id,
                },
                status=201,
            )
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid driver_id."}, status=400)


@require_POST
def driver_accept_ride_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        driver_profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)

    if driver_profile.role != "DRIVER":
        return JsonResponse({"error": "Only drivers can accept rides."}, status=403)

    try:
        body_text = request.body.decode('utf-8') if request.body else ''
    except Exception:
        body_text = ''

    if body_text.startswith('{') or request.content_type == 'application/json':
        try:
            data = json.loads(body_text) if body_text else {}
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    else:
        data = request.POST or {}

    booking_id = data.get("booking_id")
    if booking_id is None:
        return JsonResponse({"error": "booking_id is required."}, status=400)

    try:
        with transaction.atomic():
            booking = Booking.objects.get(id=booking_id)

            driver_profile = UserProfile.objects.select_for_update().get(user_id=request.user.id)

            # If driver is unavailable but not assigned to this booking, block acceptance.
            if not driver_profile.is_available and booking.driver_id != request.user.id:
                return JsonResponse({"error": "You are already unavailable or already on a trip."}, status=400)

            if booking.status != "PENDING":
                return JsonResponse({"error": "This booking is no longer pending."}, status=400)

            booking.status = "ACTIVE"
            booking.save(update_fields=["status"])

            driver_profile.is_available = False
            driver_profile.save(update_fields=["is_available"])

            schedule(
                'rides.tasks.complete_trip_task',
                booking.id,
                schedule_type='O',
                minutes=2,
            )

            try:
                RideAudit.objects.create(
                    action='booking_accepted',
                    actor=request.user,
                    actor_profile=driver_profile,
                    booking=booking,
                    details=f'Driver {request.user.username} accepted booking {booking.id}'
                )
            except Exception:
                logger.exception('Failed to write RideAudit for booking accepted (API)')

            return JsonResponse(
                {
                    "message": "Ride accepted and is now active.",
                    "booking_id": booking.id,
                    "driver_id": request.user.id,
                    "driver_locked": True,
                },
                status=200,
            )
    except Booking.DoesNotExist:
        return JsonResponse({"error": "Booking not found."}, status=404)
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid booking_id."}, status=400)


@require_POST
def manual_end_ride_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        driver_profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)

    if driver_profile.role != "DRIVER":
        return JsonResponse({"error": "Only drivers can end rides."}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    booking_id = data.get("booking_id")
    if booking_id is None:
        return JsonResponse({"error": "booking_id is required."}, status=400)

    try:
        with transaction.atomic():
            booking = Booking.objects.get(id=booking_id)

            if booking.status != "ACTIVE":
                return JsonResponse({"error": "This booking is not currently active."}, status=400)

            if booking.driver_id != request.user.id:
                return JsonResponse({"error": "You are not assigned to this ride."}, status=403)

            booking.status = "COMPLETED"
            booking.save(update_fields=["status"])

            driver_profile = UserProfile.objects.select_for_update().get(user_id=request.user.id)
            driver_profile.is_available = True
            driver_profile.save(update_fields=["is_available"])

            try:
                Schedule.objects.filter(name__icontains=str(booking.id)).delete()
            except Exception:
                pass

            try:
                RideAudit.objects.create(
                    action='booking_completed',
                    actor=request.user,
                    actor_profile=driver_profile,
                    booking=booking,
                    details=f'Booking {booking.id} completed by driver {request.user.username}'
                )
            except Exception:
                logger.exception('Failed to write RideAudit for manual end ride')

            return JsonResponse(
                {
                    "message": "Ride completed successfully.",
                    "booking_id": booking.id,
                    "driver_id": request.user.id,
                    "driver_available": True,
                },
                status=200,
            )
    except Booking.DoesNotExist:
        return JsonResponse({"error": "Booking not found."}, status=404)
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Driver profile not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid booking_id."}, status=400)


@require_POST
def cancel_booking_view(request, booking_id=None):
    """Allow a passenger to cancel their booking.

    Supports JSON API and browser POST (PRG). Notifies driver by creating a RideAudit
    and frees the driver (sets is_available=True) if assigned.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required."}, status=401)

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body and request.content_type == 'application/json' else request.POST
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    # booking_id may come from the URL (clean POST form) or from POST/JSON payload
    booking_id = booking_id or data.get('booking_id')
    if booking_id is None:
        return JsonResponse({"error": "booking_id is required."}, status=400)

    try:
        with transaction.atomic():
            booking = Booking.objects.select_for_update().get(id=booking_id)

            if booking.passenger_id != request.user.id:
                return JsonResponse({"error": "You are not authorized to cancel this booking."}, status=403)

            if booking.status == 'CANCELLED':
                return JsonResponse({"message": "Booking already cancelled."}, status=200)

            # Update booking status
            booking.status = 'CANCELLED'
            booking.save(update_fields=['status'])

            # Free the driver if assigned
            if booking.driver_id:
                try:
                    driver_profile = UserProfile.objects.select_for_update().get(user_id=booking.driver_id)
                    driver_profile.is_available = True
                    driver_profile.save(update_fields=['is_available'])
                except UserProfile.DoesNotExist:
                    driver_profile = None

            # Remove any scheduled tasks related to this booking
            try:
                Schedule.objects.filter(name__icontains=str(booking.id)).delete()
            except Exception:
                pass

            try:
                RideAudit.objects.create(
                    action='booking_cancelled',
                    actor=request.user,
                    actor_profile=getattr(request.user, 'profile', None) or getattr(request.user, 'userprofile', None),
                    booking=booking,
                    details=f'Booking {booking.id} cancelled by passenger {request.user.username}'
                )
            except Exception:
                logger.exception('Failed to write RideAudit for booking cancellation')

            # If a driver was assigned, create a targeted audit so the driver UI shows a clear message
            if booking.driver_id:
                try:
                    passenger_profile = getattr(request.user, 'profile', None) or getattr(request.user, 'userprofile', None)
                    RideAudit.objects.create(
                        action='passenger_cancelled',
                        actor=request.user,
                        actor_profile=passenger_profile,
                        booking=booking,
                        details='Passenger has cancelled the ride'
                    )
                except Exception:
                    logger.exception('Failed to write RideAudit for passenger-cancel notification')

            # If JSON/API request, respond with JSON
            is_json = (request.content_type == 'application/json') or ('application/json' in request.META.get('HTTP_ACCEPT', ''))
            if is_json:
                return JsonResponse({
                    'message': 'Booking cancelled successfully.',
                    'booking_id': booking.id,
                }, status=200)

            messages.success(request, 'Booking cancelled.')
            # Return passenger to the status page so they can see the cancelled state
            return redirect('passenger_status')

    except Booking.DoesNotExist:
        return JsonResponse({"error": "Booking not found."}, status=404)
    except ValueError:
        return JsonResponse({"error": "Invalid booking_id."}, status=400)


@login_required
@login_required
def profile_management_view(request):
    logger.info("Entered profile_management_view: method=%s, user=%s", request.method, getattr(request.user, 'username', None))
    user = request.user
    profile = getattr(user, 'userprofile', None) or getattr(user, 'profile', None)

    if request.method == 'POST':
        user_form = UserForm(request.POST, instance=user)
        profile_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        password_form = SimplePasswordChangeForm(user, request.POST) if 'old_password' in request.POST else None

        # Validate forms and log errors for diagnostics
        user_valid = user_form.is_valid()
        profile_valid = profile_form.is_valid()
        logger.info('User form valid=%s, Profile form valid=%s', user_valid, profile_valid)
        logger.info('User form errors: %s', user_form.errors)
        logger.info('Profile form errors: %s', profile_form.errors)

        forms_valid = user_valid and profile_valid
        if password_form:
            pw_valid = password_form.is_valid()
            logger.info('Password form valid=%s, errors=%s', pw_valid, password_form.errors)
            forms_valid = forms_valid and pw_valid

        if forms_valid:
            # Save user explicitly and log the saved values
            saved_user = user_form.save()
            logger.info('Saved user id=%s first_name=%s last_name=%s', saved_user.id, saved_user.first_name, saved_user.last_name)
            # Let the form handle creating/updating the UserProfile and saving the uploaded file
            # If files didn't parse (test-client oddness), try a manual multipart parse fallback
            logger.info("Profile management: request.FILES=%s, POST keys=%s, content_type=%s", list(request.FILES.keys()), list(request.POST.keys()), request.META.get('CONTENT_TYPE'))
            # Defensive fallbacks: test-client or middleware may populate internal attrs
            if not request.FILES:
                for src in ('_files', '_post'):
                    src_val = getattr(request, src, None)
                    try:
                        if isinstance(src_val, dict) and src_val:
                            request.FILES.update(src_val)
                            logger.info("Recovered files from request.%s: %s", src, list(src_val.keys()))
                            break
                    except Exception:
                        continue

            if not request.FILES and request.META.get('CONTENT_TYPE', '').startswith('multipart/form-data'):
                try:
                    mp = MultiPartParser(request.META, BytesIO(request.body), request.upload_handlers)
                    _post, _files = mp.parse()
                    # inject parsed files into request.FILES-like dict for the form
                    request.FILES.update(_files)
                    logger.info("MultipartParser fallback provided files: %s", list(_files.keys()))
                except Exception as e:
                    logger.exception("MultipartParser fallback failed: %s", e)

            profile_saved = profile_form.save()
            logger.info("Profile form errors: %s", profile_form.errors.as_json() if profile_form.errors else "{}")
            logger.info("Profile saved profile_picture=%s", getattr(profile_saved, 'profile_picture', None))
            # Ensure the saved profile is linked to the user
            if not getattr(profile_saved, 'user', None):
                profile_saved.user = user
                profile_saved.save()

            if password_form:
                password_form.save()
                update_session_auth_hash(request, user)

            messages.success(request, 'Profile updated successfully.')
            return redirect('profile_management')
    else:
        user_form = UserForm(instance=user)
        profile_form = UserProfileForm(instance=profile)
        password_form = SimplePasswordChangeForm(user)

    return render(request, 'rides/profile_management.html', {
        'user_form': user_form,
        'profile_form': profile_form,
        'password_form': password_form,
    })


@login_required
@require_GET
def dashboard_view(request):
    profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)

    context = {
        "user": request.user,
        "role": profile.role if profile else None,
    }

    if profile and profile.role == "PASSENGER":
        available_drivers = UserProfile.objects.filter(role="DRIVER", is_available=True).select_related("user")
        context["available_drivers"] = available_drivers
        context["latest_booking"] = Booking.objects.filter(passenger=request.user).order_by("-pickup_time").first()
    elif profile and profile.role == "DRIVER":
        context["pending_rides"] = get_driver_bookings(request.user, status="PENDING")
        context["active_rides"] = get_driver_bookings(request.user, status="ACTIVE")

    return render(request, "rides/dashboard.html", context)


def role_required(role):
    """Decorator to enforce UserProfile.role equals `role`.

    Redirects to dashboard with a permission error message when check fails.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')

            profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)
            if profile is None:
                messages.info(request, "Please complete your profile before accessing this portal.")
                return redirect('profile_setup')

            if profile.role != role:
                messages.info(request, "You are being redirected to the correct portal.")
                if profile.role == 'DRIVER':
                    return redirect('driver_portal')
                if profile.role == 'PASSENGER':
                    return redirect('passenger_portal')
                return redirect('dashboard')

            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


@login_required
@role_required('PASSENGER')
@require_GET
def passenger_booking_view(request):
    profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)
    # Query live available drivers (profiles)
    available_drivers = UserProfile.objects.filter(role='DRIVER', is_available=True).select_related('user')
    return render(request, 'rides/passenger_booking.html', {'available_drivers': available_drivers, 'user': request.user, 'role': profile.role if profile else None})


@login_required
@role_required('PASSENGER')
@require_POST
def create_booking(request):
    """Create a booking when passenger taps a driver card.

    Accepts `driver_id` which may be a UserProfile id or a User id. Marks driver unavailable
    and creates a Booking with status PENDING, then redirects passenger to the status page.
    """
    driver_id = request.POST.get('driver_id')
    if not driver_id:
        messages.error(request, 'Driver selection required.')
        return redirect('passenger_portal')

    logger.info('Create booking requested', extra={'passenger': getattr(request.user, 'username', None), 'driver_id': driver_id})

    try:
        with transaction.atomic():
            # Try to find a UserProfile first (template passes profile.id)
            driver_profile = None
            try:
                driver_profile = UserProfile.objects.select_for_update().get(id=driver_id)
                driver_user = driver_profile.user
            except (UserProfile.DoesNotExist, ValueError):
                # Fallback: treat driver_id as a User id
                driver_user = User.objects.select_for_update().get(id=driver_id)
                driver_profile = UserProfile.objects.select_for_update().filter(user=driver_user).first()

            # Ensure driver is available
            if driver_profile and not driver_profile.is_available:
                logger.info('Create booking aborted: driver unavailable', extra={'driver': getattr(driver_user, 'username', None)})
                messages.error(request, 'Driver is no longer available. Please try another.')
                return redirect('passenger_portal')

            if driver_profile:
                driver_profile.is_available = False
                driver_profile.save(update_fields=['is_available'])

            # Create booking (Booking.passenger expects a User)
            booking = Booking.objects.create(
                passenger=request.user,
                driver=driver_user,
                status='PENDING',
            )

            logger.info('Booking created', extra={'booking_id': booking.id, 'passenger': request.user.username, 'driver': driver_user.username})
            try:
                # Record an audit entry for the booking creation
                passenger_profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)
                RideAudit.objects.create(
                    action='booking_created',
                    actor=request.user,
                    actor_profile=passenger_profile,
                    booking=booking,
                    details=f'Passenger {request.user.username} requested driver {driver_user.username}',
                )
            except Exception:
                logger.exception('Failed to create RideAudit entry for booking')
            messages.success(request, 'Ride requested. Waiting for driver confirmation.')
            return redirect('passenger_status')
    except User.DoesNotExist:
        logger.warning('Create booking failed: driver user not found', extra={'driver_id': driver_id})
        messages.error(request, 'Selected driver not found.')
        return redirect('passenger_portal')
    except Exception as exc:
        logger.exception('Create booking error', extra={'driver_id': driver_id})
        messages.error(request, 'Unable to create booking. Please try again.')
        return redirect('passenger_portal')


@login_required
@role_required('PASSENGER')
@require_GET
def passenger_waiting_view(request):
    # Show the passenger the waiting page for their most recent booking
    latest_booking = Booking.objects.filter(passenger=request.user).order_by('-pickup_time').first()
    return render(request, 'rides/passenger_waiting.html', {'booking': latest_booking})


@login_required
@role_required('PASSENGER')
@require_GET
def passenger_status_view(request):
    """Render an HTML passenger status page for the passenger's most recent booking.

    Falls back between Booking.passenger being a User or a profile. If no booking found,
    redirect back to the passenger booking portal.
    """
    passenger_profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)

    # Prefer direct User-based bookings, but fall back to profile-based bookings when model shapes vary.
    latest_booking = Booking.objects.filter(passenger=request.user).order_by('-pickup_time').first()
    if latest_booking is None and passenger_profile is not None:
        try:
            latest_booking = Booking.objects.filter(passenger=passenger_profile).order_by('-pickup_time').first()
        except Exception:
            latest_booking = None

    if latest_booking is None:
        return redirect('passenger_portal')

    # Include recent audits for this booking so passengers receive accept/decline notifications
    try:
        recent_audits = RideAudit.objects.filter(booking=latest_booking).order_by('-timestamp')[:5]
    except Exception:
        # If audit table/migrations are missing or DB error occurs, degrade gracefully
        logger.exception('Failed to load RideAudit entries for passenger_status_view')
        recent_audits = []

    return render(request, 'rides/passenger_status.html', {'booking': latest_booking, 'audits': recent_audits})


@login_required
@role_required('DRIVER')
@require_GET
def driver_portal_view(request):
    # Resolve driver profile safely (support both 'userprofile' and 'profile' related names)
    driver_profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)

    # Try to locate a pending booking assigned to this driver.
    # Handle both possible model shapes: Booking.driver may reference a User or a profile.
    pending_booking = None
    if driver_profile is not None:
        # First try filtering by profile object (if Booking uses profile FK)
        try:
            pending_booking = Booking.objects.filter(driver=driver_profile, status='PENDING').first()
        except Exception:
            pending_booking = None

        # If none found, try filtering by the underlying User
        if pending_booking is None:
            try:
                pending_booking = Booking.objects.filter(driver=driver_profile.user, status='PENDING').first()
            except Exception:
                pending_booking = None

    # If a pending booking exists, immediately redirect to the offer page (intercept)
    if pending_booking:
        return redirect('driver_offer_page', booking_id=pending_booking.id)

    # Otherwise render the normal portal with active rides
    active_rides = Booking.objects.filter(driver=(driver_profile.user if driver_profile else request.user), status='ACTIVE')
    context = {
        'active_rides': active_rides,
        'user': request.user,
        'role': getattr(driver_profile, 'role', None) if driver_profile else None,
        'is_available': getattr(driver_profile, 'is_available', True) if driver_profile else False,
    }
    return render(request, 'rides/driver_portal.html', context)





@login_required
@role_required('DRIVER')
@require_GET
def driver_offer_page(request, booking_id):
    # Attempt to resolve the booking robustly whether Booking.driver references User or a profile
    booking = None
    try:
        booking = Booking.objects.select_related('passenger').get(id=booking_id, status='PENDING')
        # Ensure this booking is assigned to this driver (user or profile)
        driver_profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)
        assigned_to_this_driver = False
        if booking.driver == request.user:
            assigned_to_this_driver = True
        elif driver_profile is not None and getattr(booking, 'driver', None) == driver_profile:
            assigned_to_this_driver = True

        if not assigned_to_this_driver:
            messages.error(request, 'No incoming ride offer was found.')
            return redirect('driver_portal')
    except Booking.DoesNotExist:
        messages.error(request, 'No incoming ride offer was found.')
        return redirect('driver_portal')

    return render(request, 'rides/driver_offer.html', {'booking': booking})


@login_required
@role_required('DRIVER')
@require_POST
def driver_offer_accept(request, booking_id):
    try:
        booking = Booking.objects.get(id=booking_id, driver=request.user, status='PENDING')
    except Booking.DoesNotExist:
        messages.error(request, 'No pending ride was found to accept.')
        return redirect('driver_portal')

    booking.status = 'ACTIVE'
    booking.save(update_fields=['status'])

    try:
        profile = request.user.profile
        profile.is_available = False
        profile.save(update_fields=['is_available'])
    except UserProfile.DoesNotExist:
        pass

    try:
        RideAudit.objects.create(
            action='booking_accepted',
            actor=request.user,
            actor_profile=getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None),
            booking=booking,
            details=f'Driver {request.user.username} accepted booking {booking.id}'
        )
    except Exception:
        logger.exception('Failed to write RideAudit for booking accept')

    messages.success(request, 'Ride accepted. You are now on the active tracker.')
    return redirect('driver_portal')


@login_required
@role_required('DRIVER')
@require_POST
def driver_offer_decline(request, booking_id):
    try:
        booking = Booking.objects.get(id=booking_id, driver=request.user, status='PENDING')
    except Booking.DoesNotExist:
        messages.error(request, 'No pending ride was found to decline.')
        return redirect('driver_portal')

    booking.status = 'CANCELLED'
    booking.save(update_fields=['status'])

    try:
        profile = request.user.profile
        profile.is_available = True
        profile.save(update_fields=['is_available'])
    except UserProfile.DoesNotExist:
        pass

    try:
        RideAudit.objects.create(
            action='booking_declined',
            actor=request.user,
            actor_profile=getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None),
            booking=booking,
            details=f'Driver {request.user.username} declined booking {booking.id}'
        )
    except Exception:
        logger.exception('Failed to write RideAudit for booking decline')

    messages.success(request, 'Ride declined. You are returned to the portal.')
    try:
        RideAudit.objects.create(
            action='booking_declined',
            actor=request.user,
            actor_profile=getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None),
            booking=booking,
            details=f'Driver {request.user.username} declined booking {booking.id}'
        )
    except Exception:
        logger.exception('Failed to write RideAudit for booking decline')
    return redirect('driver_portal')


@login_required
@role_required('PASSENGER')
@require_GET
def ride_status_view(request):
    # Resolve the passenger's latest booking and render the passenger-facing status page.
    # Support both cases where Booking.passenger may be a User or a profile with a user relation.
    latest_booking = None
    try:
        # Most common: Booking.passenger is a User
        latest_booking = Booking.objects.filter(passenger=request.user).order_by('-pickup_time').first()
    except Exception:
        # Fallback: Booking.passenger may be a profile with a user FK
        latest_booking = Booking.objects.filter(passenger__user=request.user).order_by('-pickup_time').first()

    return render(request, 'rides/passenger_status.html', {'booking': latest_booking})


@login_required
@require_GET
def driver_notifications_api(request):
    """Return pending bookings assigned to the logged-in driver as JSON."""
    try:
        profile = request.user.profile
    except UserProfile.DoesNotExist:
        return JsonResponse({"error": "Profile not found."}, status=404)

    if profile.role != "DRIVER":
        return JsonResponse({"error": "Only drivers may fetch notifications."}, status=403)

    pending = get_driver_bookings(request.user, status="PENDING").values("id", "passenger__username", "pickup_time")
    pending_list = [
        {"type": "booking", "id": b["id"], "passenger": b["passenger__username"], "pickup_time": b["pickup_time"].isoformat() if b["pickup_time"] else None}
        for b in pending
    ]

    # Include recent audit notifications for this driver so they see cancellations/updates
    audits_list = []
    try:
        recent_audits = RideAudit.objects.filter(booking__driver=request.user).order_by('-timestamp')[:10]
        for a in recent_audits:
            audits_list.append({
                'type': 'audit',
                'action': a.action,
                'booking_id': a.booking.id if a.booking else None,
                'details': a.details,
                'timestamp': a.timestamp.isoformat() if a.timestamp else None,
            })
    except Exception:
        logger.exception('Failed to load RideAudit entries for driver_notifications_api')

    return JsonResponse(pending_list + audits_list, safe=False)


@login_required
@require_GET
def audits_dashboard_view(request):
    """Simple admin/staff-only audits dashboard listing recent RideAudit entries."""
    if not request.user.is_staff:
        messages.error(request, 'Access denied.')
        return redirect('dashboard')

    audits = RideAudit.objects.select_related('actor', 'booking', 'actor_profile').order_by('-timestamp')[:200]
    return render(request, 'rides/audits_dashboard.html', {'audits': audits})


@login_required
@require_GET
def passenger_check_status(request):
    # Return most recent booking including PENDING so passenger UIs can track progress
    latest_booking = Booking.objects.filter(
        passenger=request.user,
        status__in=["PENDING", "ACTIVE", "COMPLETED", "CANCELLED"],
    ).order_by("-pickup_time").first()

    if not latest_booking:
        return JsonResponse({"status": None, "driver_name": None})

    driver_name = None
    try:
        driver_name = latest_booking.driver.username if latest_booking.driver else None
    except Exception:
        driver_name = None

    return JsonResponse({
        "status": latest_booking.status,
        "driver_name": driver_name,
        "booking_id": latest_booking.id,
    })


@login_required
@require_POST
def book_ride_view(request):
    # Support both JSON API and HTML form submissions.
    # If JSON body present, delegate to the existing API handler.
    try:
        body_text = request.body.decode('utf-8') if request.body else ''
    except Exception:
        body_text = ''

    if body_text.startswith('{') or request.content_type == 'application/json':
        return request_ride_view(request)

    # Otherwise handle as an HTML form submit and redirect to ride status page.
    driver_id = request.POST.get('driver_id')
    if not driver_id:
        messages.error(request, 'Driver selection is required.')
        return redirect('passenger_booking')

    try:
        with transaction.atomic():
            driver_profile = UserProfile.objects.select_for_update().get(user_id=driver_id)

            if driver_profile.role != 'DRIVER':
                messages.error(request, 'Selected user is not a driver.')
                return redirect('passenger_booking')

            if Booking.objects.filter(driver=driver_profile.user, status__in=['PENDING', 'ACTIVE']).exists():
                messages.error(request, 'Selected driver is currently unavailable.')
                return redirect('passenger_booking')

            # Mark driver as unavailable and create booking
            driver_profile.is_available = False
            driver_profile.save(update_fields=['is_available'])

            booking = Booking.objects.create(
                passenger=request.user,
                driver=driver_profile.user,
                status='PENDING',
            )

            messages.success(request, 'Ride requested. Waiting for driver confirmation.')
            return redirect('passenger_waiting')
    except UserProfile.DoesNotExist:
        messages.error(request, 'Driver not found.')
        return redirect('passenger_booking')


@login_required
@require_POST
def accept_ride_view(request):
    # Driver accepts a pending booking -> becomes ACTIVE
    return driver_accept_ride_view(request)


@login_required
@require_POST
def complete_ride_view(request):
    booking_id = request.POST.get('booking_id')
    if not booking_id:
        messages.error(request, 'Booking ID is required to confirm completion.')
        return redirect('dashboard')

    try:
        booking = Booking.objects.select_for_update().get(id=booking_id, status='ACTIVE')
    except Booking.DoesNotExist:
        messages.error(request, 'Active booking not found.')
        return redirect('dashboard')

    # Only a driver needs to confirm completion to mark the booking COMPLETED
    if request.user == booking.driver:
        booking.driver_completed = True
        # Mark completed immediately and free the driver
        booking.status = 'COMPLETED'
        try:
            booking.save(update_fields=['driver_completed', 'status'])
        except Exception:
            booking.save()

        try:
            driver_profile = UserProfile.objects.get(user=booking.driver)
            driver_profile.is_available = True
            driver_profile.save(update_fields=['is_available'])
        except UserProfile.DoesNotExist:
            pass

        try:
            RideAudit.objects.create(
                action='booking_completed',
                actor=request.user,
                actor_profile=getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None),
                booking=booking,
                details=f'Booking {booking.id} marked COMPLETED by {request.user.username} (driver-confirmed)'
            )
        except Exception:
            logger.exception('Failed to write RideAudit for booking completion')

        messages.success(request, 'Ride marked completed. Thank you, driver.')
    elif request.user == booking.passenger:
        # Passenger confirmation is recorded but no longer required to complete the ride
        booking.passenger_completed = True
        try:
            booking.save(update_fields=['passenger_completed'])
        except Exception:
            booking.save()
        messages.success(request, 'Thanks — your confirmation has been recorded.')
    else:
        messages.error(request, 'You are not authorized to complete this ride.')
        return redirect('dashboard')

    profile = getattr(request.user, 'userprofile', None) or getattr(request.user, 'profile', None)
    redirect_target = 'driver_portal' if profile and profile.role == 'DRIVER' else 'passenger_waiting'
    return redirect(redirect_target)


@login_required
@require_POST
def end_ride_view(request):
    # Driver ends an active booking -> becomes COMPLETED immediately
    return manual_end_ride_view(request)
