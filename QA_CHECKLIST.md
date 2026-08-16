QA Checklist: Booking & Availability

1) Manual test: driver card click-to-book (no JS)
- Start dev server: `python manage.py runserver`
- Log in as a Passenger and go to: http://127.0.0.1:8000/portal/passenger/
- Click anywhere on a driver's card (the whole card is now a submit button).
- Expect: Redirect to the waiting page; booking created with status `PENDING`.

2) Manual test: passenger status auto-refresh
- After booking, go to: http://127.0.0.1:8000/passenger-status/
- Observe the page auto-refreshes every 3 seconds and shows `Finding your driver...` for pending bookings.

3) Manual test: driver availability toggle
- Log in as the Driver and go to: http://127.0.0.1:8000/portal/driver/
- Use the `Go Offline` / `Go Online` button in the header to toggle availability.
- Confirm the header button label changes and that driver profile `is_available` updates in Django admin.

4) Automated tests
- Run tests locally:

```bash
python manage.py test rides
```

The two new tests check that booking creates a `PENDING` booking and that the toggle endpoint changes `is_available`.

Notes
- No custom JavaScript is required. Clicks submit HTML forms and browser handles navigation.
- If tests fail, run with `-v 2` for more detail: `python manage.py test rides -v 2`.
