# Run Tasks QA Guide

This guide shows you how to run the custom Django management command in a second terminal window, test the auto-cancel flow in the browser, and confirm the database updates through terminal output and Django Admin.

---

## 1) Run the command in a separate terminal window

You should keep your normal development server running in one terminal and start the background task command in another terminal.

### Terminal A: Start the Django development server

Open a terminal in your project root and run:

```powershell
cd "c:\Users\NzeDave47\Documents\Code Projects\Simple Taxi Business"
python manage.py runserver
```

Leave this terminal running.

### Terminal B: Start the auto-task command

Open a second terminal in the same project root and run:

```powershell
cd "c:\Users\NzeDave47\Documents\Code Projects\Simple Taxi Business"
python manage.py run_tasks
```

This will keep running in the background and check for expired pending bookings every 5 seconds.

> If you want to stop it later, press Ctrl+C in Terminal B.

---

## 2) Browser test case: verify auto-expiration and cancellation

Use this test flow as a Passenger and Driver to verify that an unattended ride request expires after 2 minutes.

### Test setup

Make sure you have:
- at least one Passenger user account
- at least one Driver user account
- a Driver profile for the driver
- a Passenger profile for the passenger
- at least one available cab or driver booking flow that can create a pending booking

### Step-by-step test

#### A. Passenger creates a pending booking

1. Open your browser and log in as the Passenger.
2. Navigate to the ride booking page or API-driven booking flow used by your app.
3. Create a booking/request that assigns a Driver.
4. Confirm the booking is created and remains in a pending state.

#### B. Leave it unattended for 2 minutes

1. Do not approve, cancel, or complete the booking.
2. Wait at least 2 minutes.
3. The custom management command will detect the old pending booking and cancel it automatically.

#### C. Driver side check

1. Open a separate browser session or another browser window and log in as the Driver.
2. Refresh the relevant page or check the booking/ride status view.
3. Confirm that the previously assigned ride request is no longer active and the driver is now available again.

### Expected result

After about 2 minutes of inactivity, the booking should:
- change status from PENDING to CANCELLED
- release the assigned driver by setting their availability back to True

---

## 3) How to verify the update in terminal logs and Django Admin

### A. Check the terminal logs

In Terminal B, you should see output similar to:

```text
Auto-cancelled expired booking #12 at 14:30:05
```

This confirms the management command detected an expired pending booking and processed it.

### B. Check the Django Admin panel

1. Start the Django admin server if needed.
2. Open the admin site in your browser.
3. Log in with a superuser account.
4. Go to the Booking model.
5. Find the booking you tested.

### What to verify in the admin panel

- The booking status should now be CANCELLED.
- The assigned driver should be listed on that booking record.
- The driver profile should show the driver as available again if you inspect the UserProfile record.

### C. Optional: verify the driver profile directly

In the admin panel:
1. Open the UserProfile model.
2. Find the driver you used in the test.
3. Confirm that the is_available field is True.

---

## Quick summary

If everything is working:
- Terminal A keeps your site running.
- Terminal B processes expired bookings.
- After 2 minutes of no action, the pending booking is cancelled automatically.
- The terminal log shows the cancellation message.
- Django Admin shows the booking updated to CANCELLED and the driver availability reset.

If you run into issues, check:
- that both terminals are in the correct project folder
- that your database contains a real pending booking older than 2 minutes
- that the driver profile exists and is linked correctly
