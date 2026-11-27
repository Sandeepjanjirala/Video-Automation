"""One process preserves the application's process-local browser write token."""
bind = '0.0.0.0:8788'
workers = 1
worker_class = 'gthread'
threads = 2
timeout = 180
graceful_timeout = 180
accesslog = None  # URLs may contain sensitive query parameters.
errorlog = '-'
capture_output = True
