web: gunicorn taxi_project.wsgi:application --bind 0.0.0.0:$PORT --log-file -
worker: python manage.py qcluster
