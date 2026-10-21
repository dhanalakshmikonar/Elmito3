from django.dispatch import Signal

# Sent (after the database commit) once an order has been paid and fulfilled.
# Receivers must not be relied on: failures are logged and never affect the order.
order_paid = Signal()

# Sent after commit whenever an order's status changes through the status service.
# kwargs: order, old_status, new_status
order_status_changed = Signal()
