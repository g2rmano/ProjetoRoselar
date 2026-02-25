from django.urls import path
from . import views

app_name = "calendar_app"

urlpatterns = [
    # Páginas principais
    path("", views.calendar_view, name="calendar"),
    path("proximos/", views.upcoming_events, name="upcoming"),
    path("atrasados/", views.overdue_events, name="overdue"),
    path("novo/", views.create_event, name="create_event"),

    # Detalhe do evento (página separada — mantida para links externos)
    path("evento/<int:event_id>/", views.event_detail, name="event_detail"),
    path("evento/<int:event_id>/concluir/", views.event_mark_done, name="event_mark_done"),
    path("evento/<int:event_id>/cancelar/", views.event_mark_canceled, name="event_mark_canceled"),

    # Lembretes (AJAX)
    path("lembrete/<int:reminder_id>/dispensar/", views.reminder_dismiss, name="reminder_dismiss"),
    path("lembrete/<int:reminder_id>/lido/", views.reminder_mark_read, name="reminder_mark_read"),

    # API JSON — popup inline
    path("api/lembretes/", views.reminders_api, name="reminders_api"),
    path("api/evento/<int:event_id>/", views.api_event_detail, name="api_event_detail"),
    path("api/evento/<int:event_id>/salvar/", views.api_event_update, name="api_event_update"),
    path("api/evento/<int:event_id>/concluir/", views.api_event_done, name="api_event_done"),
    path("api/evento/<int:event_id>/cancelar/", views.api_event_cancel, name="api_event_cancel"),
    path("api/evento/<int:event_id>/excluir/", views.api_event_delete, name="api_event_delete"),
    path("api/evento/<int:event_id>/anexo/", views.api_attachment_upload, name="api_attachment_upload"),
    path("api/evento/criar/", views.api_event_create, name="api_event_create"),
    path("api/anexo/<int:attachment_id>/download/", views.api_attachment_download, name="api_attachment_download"),
    path("api/anexo/<int:attachment_id>/excluir/", views.api_attachment_delete, name="api_attachment_delete"),

    # Tags / Etiquetas API
    path("api/tags/", views.api_tags_list, name="api_tags_list"),
    path("api/tags/criar/", views.api_tag_create, name="api_tag_create"),
    path("api/tags/<int:tag_id>/salvar/", views.api_tag_update, name="api_tag_update"),
    path("api/tags/<int:tag_id>/excluir/", views.api_tag_delete, name="api_tag_delete"),
    path("api/evento/<int:event_id>/tag/<int:tag_id>/", views.api_event_tag_toggle, name="api_event_tag_toggle"),
]
