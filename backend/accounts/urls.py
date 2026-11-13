from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf", views.csrf_view),
    path("auth/signup", views.SignupView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/me", views.MeView.as_view()),
    path("users", views.UserListView.as_view()),
    path("users/me", views.MyProfileView.as_view()),
    path("users/me/avatar/presign", views.AvatarPresignView.as_view()),
    path("users/<int:user_id>", views.UserDetailView.as_view()),
    path("users/<int:user_id>/avatar", views.AvatarView.as_view()),
    path("presence", views.PresenceView.as_view()),
]
