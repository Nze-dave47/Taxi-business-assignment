from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .views import (
    accept_ride_view,
    book_ride_view,
    dashboard_view,
    driver_offer_accept,
    driver_offer_decline,
    driver_offer_page,
    driver_notifications_api,
    driver_portal_view,
    end_ride_view,
    passenger_booking_view,
    ride_status_view,
)

urlpatterns = [
    # Public passenger registration and intentionally unlisted driver onboarding.
    path('login/', views.login_view, name='login'),
    path('register/', views.passenger_register_view, name='register'),
    path('secret-driver-onboarding-portal/', views.driver_register_view, name='driver_register'),

    # Dashboard and portal entry points
    path('', dashboard_view, name='dashboard'),
    path('portal/passenger/', passenger_booking_view, name='passenger_portal'),
    path('create-booking/', views.create_booking, name='create_booking'),
    path('portal/driver/', driver_portal_view, name='driver_portal'),
    path('admin/audits/', views.audits_dashboard_view, name='audits_dashboard'),
    path('toggle-availability/', views.toggle_availability_view, name='toggle_availability'),
    path('booking/', passenger_booking_view, name='passenger_booking'),

    # Passenger booking and ride flow
    path('book-ride/', book_ride_view, name='book_ride'),
    path('waiting/', views.passenger_waiting_view, name='passenger_waiting'),
    path('ride-status/', ride_status_view, name='ride_status'),
    path('ride-status/<int:booking_id>/', ride_status_view, name='ride_status_detail'),
    path('ride-status/<int:booking_id>/<str:status>/', ride_status_view, name='ride_status_by_status'),
    path('ride-status/<int:booking_id>/<str:status>/<str:driver_username>/', ride_status_view, name='ride_status_with_driver'),
    # Passenger-facing HTML status page (auto-refreshing)
    path('status/', views.passenger_status_view, name='passenger_status'),

    # Driver offer and ride management
    path('portal/offer/<int:booking_id>/', driver_offer_page, name='driver_offer_page'),
    path('portal/offer/<int:booking_id>/accept/', driver_offer_accept, name='driver_offer_accept'),
    path('portal/offer/<int:booking_id>/decline/', driver_offer_decline, name='driver_offer_decline'),
    # Clean, JS-free endpoints for driver offer flows
    path('driver-offer/<int:booking_id>/', driver_offer_page, name='driver_offer_page_clean'),
    path('accept-ride/<int:booking_id>/', driver_offer_accept, name='accept_ride'),
    path('decline-ride/<int:booking_id>/', driver_offer_decline, name='decline_ride'),
    path('accept-ride/', accept_ride_view, name='accept_ride'),
    path('complete-ride/', views.complete_ride_view, name='complete_ride'),
    path('end-ride/', end_ride_view, name='end_ride'),
    path('profile/', views.profile_management_view, name='profile_management'),
    # Passenger cancel endpoint - support URL path with booking id for clean form POSTs
    path('cancel-booking/<int:booking_id>/', views.cancel_booking_view, name='cancel_booking'),
    # Backward-compatible endpoint that accepts booking_id in POST body (kept for API clients)
    path('cancel-booking/', views.cancel_booking_view, name='cancel_booking_no_id'),

    # API endpoints
    path('api/check-notifications/', views.check_notifications, name='check_notifications'),
    path('api/passenger-status/', views.passenger_check_status, name='passenger_status_api'),
    path('api/driver-notifications/', driver_notifications_api, name='driver_notifications_api'),
    path('api/available-cabs/', views.available_cabs_view, name='available_cabs_api'),
    path('api/book-cab/', views.book_cab_view, name='book_cab_api'),
    path('api/book-driver/', views.book_driver_view, name='book_driver_api'),
    path('api/request-ride/', views.request_ride_view, name='request_ride_api'),
    path('api/toggle-availability/', views.toggle_availability_view, name='toggle_availability_api'),
    path('api/driver/accept-ride/', views.driver_accept_ride_view, name='driver_accept_ride_api'),
    path('api/driver/end-ride/', views.manual_end_ride_view, name='driver_end_ride_api'),

    # Authentication routes
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('password_change/', auth_views.PasswordChangeView.as_view(template_name='registration/password_change_form.html'), name='password_change'),
    path('password_change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('password_reset/', auth_views.PasswordResetView.as_view(template_name='registration/password_reset_form.html'), name='password_reset'),
    path('password_reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),
]

