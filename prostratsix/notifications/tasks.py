from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from .models import Notification


@shared_task()
def send_notification_email(notification_id: int) -> None:
    """Send the e-mail representation of a single notification."""
    try:
        notification = Notification.objects.select_related("recipient").get(
            id=notification_id,
        )
    except Notification.DoesNotExist:
        return

    recipient = notification.recipient
    if not recipient.email:
        return

    context = {"notification": notification, "user": recipient}
    subject = notification.title
    text_body = render_to_string("notifications/email/notification.txt", context)
    html_body = render_to_string("notifications/email/notification.html", context)

    send_mail(
        subject=subject,
        message=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient.email],
        html_message=html_body,
    )
