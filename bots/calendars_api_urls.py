from django.urls import path

from . import calendars_api_views

urlpatterns = [
    path("calendars", calendars_api_views.CalendarListCreateView.as_view(), name="calendar-list-create"),
    path("calendars/<str:object_id>", calendars_api_views.CalendarDetailView.as_view(), name="calendar-detail"),
]
